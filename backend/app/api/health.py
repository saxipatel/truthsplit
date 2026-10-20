import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session

router = APIRouter(prefix="/health", tags=["health"])
log = logging.getLogger(__name__)


@router.get("/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(session: Session = Depends(get_session)) -> JSONResponse:
    """Ready means the metadata database answers and migrations have been applied."""
    try:
        revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    except SQLAlchemyError as exc:
        log.warning("readiness check failed", extra={"error": type(exc).__name__})
        return JSONResponse({"status": "unavailable", "database": "unreachable"}, status_code=503)
    if revision is None:
        return JSONResponse({"status": "unavailable", "database": "not migrated"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok", "schema_revision": revision})
