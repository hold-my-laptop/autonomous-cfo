import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import Settings, get_settings
from api.domain.importer import StatementImportError
from api.errors import Problem, install_error_handlers
from api.routers import data, demo, events, forecast, health, mandate, runs
from api.state import InMemoryStateRepository, StateNotLoadedError, StateRepository

API_PREFIX = "/api/v1"
logger = logging.getLogger(__name__)


def build_repository(settings: Settings) -> StateRepository:
    """PostgreSQL when DATABASE_URL is set, otherwise in memory."""
    if settings.sqlalchemy_url:
        from api.persistence import PostgresStateRepository

        return PostgresStateRepository(settings)
    return InMemoryStateRepository(settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            app.state.repository.start()
        except (StatementImportError, StateNotLoadedError):
            logger.exception("demo state not loaded at startup")
        yield
        app.state.repository.close()

    app = FastAPI(
        title="Vero API",
        version="0.1.0",
        description=(
            "Vero Cash: cash forecast, owner rules and simulated actions for small businesses."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.repository = build_repository(settings)
    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key"],
        allow_credentials=False,
    )

    api = APIRouter(
        prefix=API_PREFIX,
        responses={
            "default": {
                "model": Problem,
                "content": {"application/problem+json": {}},
                "description": "Error (RFC 9457 problem details)",
            }
        },
    )
    api.include_router(health.router)
    api.include_router(data.router)
    api.include_router(mandate.router)
    api.include_router(forecast.router)
    api.include_router(events.router)
    api.include_router(runs.router)
    api.include_router(demo.router)
    app.include_router(api)
    return app


app = create_app()
