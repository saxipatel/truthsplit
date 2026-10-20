import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.api.deps import SessionDep
from app.comparison.matching import ComparisonError
from app.comparison.metrics import MetricEvaluationError, MetricSpec
from app.db.investigations import InvestigationSide, create_metric_definition, run_investigation
from app.models import ComparisonResult, Dataset, EvidenceRecord, Finding, Investigation, MetricDefinition
from app.schemas.imports import Page
from app.schemas.investigations import (
    ComparisonOut,
    EvidenceOut,
    FindingOut,
    InvestigationCreate,
    InvestigationOut,
    InvestigationSummary,
    MetricDefinitionCreate,
    MetricDefinitionOut,
)

router = APIRouter(prefix="/api", tags=["investigations"])

Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=1000)]


def _get(session, model, id_: uuid.UUID, label: str):
    obj = session.get(model, id_)
    if obj is None:
        raise HTTPException(404, detail=f"{label} not found.")
    return obj


@router.post("/metric-definitions", response_model=MetricDefinitionOut, status_code=status.HTTP_201_CREATED)
def create_definition(body: MetricDefinitionCreate, session: SessionDep) -> MetricDefinition:
    spec = MetricSpec.model_validate(body.model_dump(exclude={"name", "description"}))
    return create_metric_definition(session, body.name, body.description, spec)


@router.get("/metric-definitions", response_model=list[MetricDefinitionOut])
def list_definitions(session: SessionDep, name: str | None = None) -> list[MetricDefinition]:
    stmt = select(MetricDefinition).order_by(MetricDefinition.name, MetricDefinition.version.desc())
    if name:
        stmt = stmt.where(MetricDefinition.name == name)
    return list(session.scalars(stmt))


@router.get("/metric-definitions/{definition_id}", response_model=MetricDefinitionOut)
def get_definition(definition_id: uuid.UUID, session: SessionDep) -> MetricDefinition:
    return _get(session, MetricDefinition, definition_id, "Metric definition")


@router.post("/investigations", response_model=InvestigationOut, status_code=status.HTTP_201_CREATED)
def create_investigation(body: InvestigationCreate, session: SessionDep) -> InvestigationOut:
    sides = []
    for ref in (body.left, body.right):
        sides.append(
            InvestigationSide(
                _get(session, Dataset, ref.dataset_id, "Dataset"),
                _get(session, MetricDefinition, ref.metric_definition_id, "Metric definition"),
            )
        )
    try:
        investigation = run_investigation(session, body.title, *sides)
    except (MetricEvaluationError, ComparisonError) as exc:
        session.rollback()
        raise HTTPException(422, detail=str(exc)) from exc
    return _detail(session, investigation)


def _detail(session, inv: Investigation) -> InvestigationOut:
    comparison = session.get(ComparisonResult, inv.id)
    return InvestigationOut.model_validate(
        {
            **InvestigationSummary.model_validate(inv).model_dump(),
            "left_metric": MetricDefinitionOut.model_validate(inv.left_metric),
            "right_metric": MetricDefinitionOut.model_validate(inv.right_metric),
            "inputs": inv.inputs,
            "comparison": ComparisonOut.model_validate(comparison) if comparison else None,
            "findings": inv.findings,
            "config_json": inv.config_json,
            "engine_version": inv.engine_version,
        }
    )


@router.get("/investigations", response_model=list[InvestigationSummary])
def list_investigations(session: SessionDep, offset: Offset = 0, limit: Limit = 100) -> list[Investigation]:
    return list(
        session.scalars(
            select(Investigation).order_by(Investigation.created_at.desc()).offset(offset).limit(limit)
        )
    )


@router.get("/investigations/{investigation_id}", response_model=InvestigationOut)
def get_investigation(investigation_id: uuid.UUID, session: SessionDep) -> InvestigationOut:
    return _detail(session, _get(session, Investigation, investigation_id, "Investigation"))


@router.get("/investigations/{investigation_id}/findings", response_model=list[FindingOut])
def list_findings(investigation_id: uuid.UUID, session: SessionDep) -> list[Finding]:
    return _get(session, Investigation, investigation_id, "Investigation").findings


def _finding(session, investigation_id: uuid.UUID, finding_id: uuid.UUID) -> Finding:
    finding = session.get(Finding, finding_id)
    if finding is None or finding.investigation_id != investigation_id:
        raise HTTPException(404, detail="Finding not found.")
    return finding


@router.get(
    "/investigations/{investigation_id}/findings/{finding_id}/evidence", response_model=Page[EvidenceOut]
)
def list_evidence(
    investigation_id: uuid.UUID,
    finding_id: uuid.UUID,
    session: SessionDep,
    offset: Offset = 0,
    limit: Limit = 100,
) -> Page:
    _finding(session, investigation_id, finding_id)
    total = session.scalar(select(func.count()).where(EvidenceRecord.finding_id == finding_id))
    rows = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.finding_id == finding_id)
        .order_by(EvidenceRecord.id)
        .offset(offset)
        .limit(limit)
    )
    return Page(total=total, offset=offset, items=[EvidenceOut.model_validate(r) for r in rows])


CSV_COLUMNS = [
    "finding_type",
    "evidence_status",
    "side",
    "business_key",
    "source_record_id",
    "left_amount",
    "right_amount",
    "contribution",
    "left_rows",
    "right_rows",
]


@router.get("/investigations/{investigation_id}/evidence.csv")
def export_evidence_csv(investigation_id: uuid.UUID, session: SessionDep) -> StreamingResponse:
    """Every evidence row of every finding, one line each. Row lists are the source row numbers."""
    _get(session, Investigation, investigation_id, "Investigation")
    rows = session.execute(
        select(Finding.type, Finding.evidence_status, EvidenceRecord)
        .join(EvidenceRecord, EvidenceRecord.finding_id == Finding.id)
        .where(Finding.investigation_id == investigation_id)
        .order_by(Finding.step_order.nulls_last(), EvidenceRecord.id)
    )

    def numbers(detail: dict, side: str) -> str:
        return " ".join(str(r["row_number"]) for r in detail.get(f"{side}_rows", []))

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_COLUMNS)
        for ftype, fstatus, e in rows:
            writer.writerow(
                [
                    ftype.value,
                    fstatus.value,
                    e.side.value,
                    e.business_key,
                    e.source_record_id,
                    e.left_amount,
                    e.right_amount,
                    e.contribution,
                    numbers(e.detail, "left"),
                    numbers(e.detail, "right"),
                ]
            )
            if buf.tell() > 64 * 1024:
                yield buf.getvalue()
                buf.seek(0)
                buf.truncate()
        yield buf.getvalue()

    headers = {"Content-Disposition": f'attachment; filename="investigation-{investigation_id}-evidence.csv"'}
    return StreamingResponse(generate(), media_type="text/csv", headers=headers)
