"""AI service (contract section 3): a thin agent runtime in front of the model.

Endpoints
  GET  /health/live, /health  cheap liveness (no model call)
  GET  /health/model          deep check: can we reach the model?
  POST /v1/agent/runs         start an agent run (202; works in the background)
  POST /v1/explain            plain-language texts for recommendations
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status

from .agent import run_agent
from .backend_client import Backend, HttpBackend
from .config import Settings, get_settings
from .explain import explain
from .llm import LLM, build_llm
from .schemas import AgentRunStart, ExplainRequest, ExplainResult, ModelHealth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("ai")


def create_app(settings: Settings | None = None, llm: LLM | None = None, backend: Backend | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.llm = llm or build_llm(settings)
        app.state.backend = backend or HttpBackend(settings)
        app.state.tasks = {}
        log.info("AI service up: llm_mode=%s backend=%s", app.state.llm.mode, settings.api_internal_url)
        yield
        for t in app.state.tasks.values():
            t.cancel()
        if isinstance(app.state.backend, HttpBackend):
            await app.state.backend.aclose()

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

    return app


async def _guarded(start: AgentRunStart, llm: LLM, backend: Backend, settings: Settings) -> None:
    try:
        await run_agent(start, llm, backend, settings)
    except Exception:  # noqa: BLE001 - never crash the service; backend deadline -> FALLBACK
        log.exception("run %s crashed", start.run_id)


app = create_app()
