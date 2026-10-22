from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, imports, investigations, memory, sources, system
from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="TruthSplit", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.include_router(health.router)
    app.include_router(imports.router)
    app.include_router(investigations.router)
    app.include_router(sources.router)
    app.include_router(memory.router)
    app.include_router(system.router)
    return app


app = create_app()
