import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.adapters.postgres import SourceError, source_profiles
from app.api.deps import SessionDep, SettingsDep
from app.models import Investigation
from app.reporting.report import build_report, render_html
from app.seed.load import load_demo

router = APIRouter(prefix="/api", tags=["reports and system"])

REPORT_CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"


@router.get("/investigations/{investigation_id}/export")
def export_report(
    investigation_id: uuid.UUID,
    session: SessionDep,
    format: Literal["json", "html"] = "json",
    download: bool = False,
):
    inv = session.get(Investigation, investigation_id)
    if inv is None:
        raise HTTPException(404, detail="Investigation not found.")
    report = build_report(session, inv)
    disposition = (
        {"Content-Disposition": f'attachment; filename="truthsplit-{investigation_id}.{format}"'}
        if download
        else {}
    )
    if format == "html":
        return HTMLResponse(
            render_html(report),
            headers={
                "Content-Security-Policy": REPORT_CSP,
                "X-Content-Type-Options": "nosniff",
                **disposition,
            },
        )
    return JSONResponse(report, headers=disposition)


@router.get("/settings")
def settings_summary(settings: SettingsDep) -> dict:
    """Limits and safety settings in effect. Never includes credentials or connection URLs."""
    return {
        "max_upload_mb": settings.max_upload_mb,
        "max_rows": settings.max_rows,
        "statement_timeout_ms": settings.statement_timeout_ms,
        "allowed_source_hosts": sorted(settings.allowed_source_host_set),
        "source_connections": sorted(source_profiles()),
        "supported_file_types": ["csv", "xlsx", "xls"],
        "csv_encodings": ["utf-8", "cp1252", "latin-1"],
    }


@router.post("/demo")
def load_demo_data(session: SessionDep, settings: SettingsDep) -> dict:
    """Load the synthetic demo investigations (idempotent). Needs the `demo` source connection."""
    try:
        return load_demo(session, settings)
    except SourceError as exc:
        session.rollback()
        raise HTTPException(502, detail=str(exc)) from exc
