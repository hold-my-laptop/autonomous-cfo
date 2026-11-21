"""AI service (contract section 3): a thin agent runtime in front of the model.

Endpoints
  GET  /health/live, /health  cheap liveness (no model call)
  GET  /health/model          deep check: can we reach the model?
  POST /v1/agent/runs         start an agent run (202, background): Cash agent, or with
                              kind PROOF an investigation of one finding (submit_finding)
  POST /v1/explain            plain-language texts for recommendations
  POST /v1/documents/extract  invoice/notice PDF -> structured fields with sources (internal, token)
  POST /v1/documents/text     PDF / image / text upload -> plain text, OCR for scans (internal, token)
  POST /v1/rag/answer         answer from records the backend retrieved (internal, token)
  POST /v1/chat               owner questions, answered with read-only tools (no token: the
                              web app calls it through nginx; it cannot change any data)
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status

from . import tracing
from .agent import run_agent
from .chat import ChatRequest, ChatResponse, HttpPublicApi, PublicApi
from .chat import answer as chat_answer
from .backend_client import Backend, HttpBackend
from .config import Settings, get_settings
from .documents import MAX_BYTES, DocumentError, read_document_async
from .extraction import ExtractError, ExtractRequest, ExtractResponse, extract
from .explain import explain
from .investigation import run_investigation
from .llm import LLM, build_llm
from .rag import AskRequest, AskResponse
from .rag import answer as rag_answer
from .schemas import AgentRunStart, ExplainRequest, ExplainResult, ModelHealth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai")


def create_app(settings: Settings | None = None, llm: LLM | None = None, backend: Backend | None = None,
               public: PublicApi | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.llm = llm or build_llm(settings)
        app.state.backend = backend or HttpBackend(settings)
        app.state.tasks = {}
        app.state.public = public or HttpPublicApi(settings)
        app.state.chat_slots = asyncio.Semaphore(2)  # one GPU: keep chat from starving agent runs
        if settings.mlflow_tracking_uri:
            try:  # never block start-up on MLflow
                await asyncio.wait_for(asyncio.to_thread(tracing.setup, settings), timeout=15)
            except TimeoutError:
                log.warning("MLflow tracing off: %s did not answer in 15 s", settings.mlflow_tracking_uri)
        log.info("AI service up: llm_mode=%s backend=%s", app.state.llm.mode, settings.api_internal_url)
        yield
        for t in app.state.tasks.values():
            t.cancel()
        if isinstance(app.state.backend, HttpBackend):
            await app.state.backend.aclose()
        if isinstance(app.state.public, HttpPublicApi):
            await app.state.public.aclose()

    app = FastAPI(title="Vero AI service", lifespan=lifespan)

    def require_token(authorization: str | None = Header(default=None)) -> None:
        if settings.internal_token is None:
            return
        if authorization != f"Bearer {settings.internal_token.get_secret_value()}":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token")

    @app.get("/health/live")
    @app.get("/health")
    async def live():
        return {"status": "ok"}

    @app.get("/health/model", response_model=ModelHealth)
    async def model_health(request: Request):
        m: LLM = request.app.state.llm
        t0 = time.perf_counter()
        try:
            name = await asyncio.wait_for(m.model_name(), timeout=10)
            if m.mode == "live":
                await asyncio.wait_for(m.chat([{"role": "user", "content": "ping"}], max_tokens=1), timeout=15)
            return ModelHealth(ok=True, model=name, mode=m.mode, latency_ms=int((time.perf_counter() - t0) * 1000))
        except Exception as e:  # noqa: BLE001
            return ModelHealth(ok=False, model=settings.llm_model or "?", mode=m.mode,
                               latency_ms=int((time.perf_counter() - t0) * 1000), error=f"{type(e).__name__}: {e}"[:300])

    @app.post("/v1/agent/runs", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_token)])
    async def start_run(start: AgentRunStart, request: Request):
        tasks: dict[str, asyncio.Task] = request.app.state.tasks
        if start.run_id in tasks and not tasks[start.run_id].done():
            return {"accepted": True}
        task = asyncio.create_task(_guarded(start, request.app.state.llm, request.app.state.backend, settings))
        tasks[start.run_id] = task
        task.add_done_callback(lambda _t, rid=start.run_id: tasks.pop(rid, None))
        return {"accepted": True}

    @app.post("/v1/explain", response_model=ExplainResult, dependencies=[Depends(require_token)])
    async def explain_route(req: ExplainRequest, request: Request):
        return await explain(req, request.app.state.llm, settings)

    @app.post("/v1/rag/answer", response_model=AskResponse, dependencies=[Depends(require_token)])
    async def rag_route(req: AskRequest, request: Request):
        """Never fails on model trouble: falls back to listing the retrieved records."""
        return await rag_answer(req, request.app.state.llm, settings)

    @app.post("/v1/documents/extract", response_model=ExtractResponse, response_model_by_alias=True,
              dependencies=[Depends(require_token)])
    async def extract_route(req: ExtractRequest, request: Request):
        """Contract DATA_CONTRACTS §3. 422 {code, message} for unreadable input; model trouble
        and failed checks come back as status FAILED with errors (backend uses its fallback)."""
        try:
            return await extract(req, request.app.state.llm, settings)
        except ExtractError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                                detail={"code": e.code, "message": e.message}) from e

    @app.post("/v1/documents/text", dependencies=[Depends(require_token)])
    async def text_route(file: UploadFile = File(...), force_ocr: bool = Form(False)):
        """Text of an uploaded contract. Read-only and stateless: nothing is stored."""
        data = await file.read(MAX_BYTES + 1)
        try:
            doc = await read_document_async(data, file.filename or "", file.content_type or "", force_ocr)
        except DocumentError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)) from e
        except TimeoutError as e:
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                                detail="Reading the document took too long.") from e
        return {"filename": file.filename, "text": doc.text, **doc.summary()}

    @app.post("/v1/chat", response_model=ChatResponse)
    async def chat_route(req: ChatRequest, request: Request):
        slots: asyncio.Semaphore = request.app.state.chat_slots
        if slots.locked():
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Vero is busy, try again")
        async with slots:
            return await chat_answer(req, request.app.state.llm, request.app.state.public, settings)

    return app


async def _guarded(start: AgentRunStart, llm: LLM, backend: Backend, settings: Settings) -> None:
    """A Proof run investigates one finding; every other run is the Cash agent."""
    run = run_investigation if start.kind == "PROOF" else run_agent
    try:
        await run(start, llm, backend, settings)
    except Exception:  # noqa: BLE001 - never crash the service; backend deadline -> FALLBACK
        log.exception("run %s crashed", start.run_id)


app = create_app()
