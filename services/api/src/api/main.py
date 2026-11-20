from fastapi import APIRouter, FastAPI

from api.routers import health

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Vero API",
        version="0.1.0",
        description=(
            "Vero Cash: cash forecast, owner rules and simulated actions for small businesses."
        ),
    )

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(health.router)
    app.include_router(api)
    return app


app = create_app()
