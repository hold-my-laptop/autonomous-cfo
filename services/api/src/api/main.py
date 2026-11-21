from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from api.agent import AgentClient, AgentOrchestrator, HttpAgentClient
from api.config import Settings, get_settings
from api.errors import Problem, install_error_handlers
from api.proof_runs import ProofPipeline
from api.routers import (
    audit,
    data,
    demo,
    events,
    forecast,
    health,
    internal,
    mandate,
    proof,
    rag,
    recommendations,
    runs,
)
from api.startup import StateLoader
from api.state import InMemoryStateRepository, StateRepository

API_PREFIX = "/api/v1"


def operation_id(route: APIRoute) -> str:
    """The handler's name, e.g. `get_forecast`, so generated clients get readable names."""
    return route.name


def build_repository(settings: Settings) -> StateRepository:
    """PostgreSQL when DATABASE_URL is set, otherwise in memory."""
    if settings.sqlalchemy_url:
        from api.persistence import PostgresStateRepository

        return PostgresStateRepository(settings)
    return InMemoryStateRepository(settings)


def create_app(
    settings: Settings | None = None, agent_client: AgentClient | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.loader.load()
        yield
        app.state.agent.close()
        app.state.proof.close()
        app.state.repository.close()

    app = FastAPI(
        title="Vero API",
        version="0.1.0",
        description=(
            "Vero Cash: cash forecast, owner rules and simulated actions for small businesses."
        ),
        lifespan=lifespan,
        generate_unique_id_function=operation_id,
    )
    app.state.settings = settings
    app.state.repository = build_repository(settings)
    app.state.agent_client = agent_client or HttpAgentClient(settings)
    app.state.agent = AgentOrchestrator(app.state.repository, settings, app.state.agent_client)
    app.state.proof = ProofPipeline(app.state.repository, settings, app.state.agent_client)
    app.state.loader = StateLoader(
        app.state.repository, app.state.agent, settings.state_load_retry_s, app.state.proof
    )
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
                "content": {
                    "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
                },
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
    api.include_router(recommendations.router)
    api.include_router(audit.router)
    api.include_router(proof.router)
    api.include_router(rag.router)
    api.include_router(demo.router)
    app.include_router(api)
    app.include_router(internal.router)
    return app


app = create_app()
