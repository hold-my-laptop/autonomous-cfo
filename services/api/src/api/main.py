import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse

from api.config import Settings, get_settings
from api.domain.importer import StatementImportError
from api.routers import data, demo, forecast, health, mandate
from api.state import InMemoryStateRepository, StateNotLoadedError

API_PREFIX = "/api/v1"
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            app.state.repository.reset()
        except StatementImportError:
            logger.exception("demo state not loaded at startup")
        yield

    app = FastAPI(
        title="Vero API",
        version="0.1.0",
        description=(
            "Vero Cash: cash forecast, owner rules and simulated actions for small businesses."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.repository = InMemoryStateRepository(settings)

    @app.exception_handler(StateNotLoadedError)
    async def state_not_loaded(request: Request, exc: StateNotLoadedError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)}
        )

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    api.include_router(data.router)
    api.include_router(mandate.router)
    api.include_router(forecast.router)
    api.include_router(demo.router)
    app.include_router(api)
    return app


app = create_app()
