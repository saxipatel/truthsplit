import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.adapters.files import IngestionError
from app.api.deps import SessionDep, SettingsDep
from app.comparison.metrics import MetricEvaluationError
from app.db.simulations import run_simulation
from app.memory import service
from app.models import Investigation, ResolutionNote, Simulation
from app.models.investigations import Decision, InvestigationStatus, SimulationRule
from app.simulation.whatif import SimulationError, SimulationRequest

router = APIRouter(prefix="/api", tags=["simulation and memory"])


def _investigation(session, investigation_id: uuid.UUID) -> Investigation:
    inv = session.get(Investigation, investigation_id)
    if inv is None:
        raise HTTPException(404, detail="Investigation not found.")
    return inv


# ---------------------------------------------------------------------------- simulations


class SimulationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    investigation_id: uuid.UUID
    rule_type: SimulationRule
    parameters_json: dict
    before_left: Decimal
    before_right: Decimal
    before_delta: Decimal
    after_left: Decimal
    after_right: Decimal
    after_delta: Decimal
    resolved_amount: Decimal = Field(
        description="|before_delta| - |after_delta|; negative when the change widens the gap"
    )
    affected_rows: int
    result_json: dict
    created_at: datetime


@router.post(
    "/investigations/{investigation_id}/simulations",
    response_model=SimulationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_simulation(
    investigation_id: uuid.UUID,
    rule: Annotated[SimulationRequest, Body()],
    session: SessionDep,
    settings: SettingsDep,
) -> Simulation:
    inv = _investigation(session, investigation_id)
    try:
        return run_simulation(session, settings, inv, rule)
    except (SimulationError, MetricEvaluationError, IngestionError) as exc:
        session.rollback()
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/investigations/{investigation_id}/simulations", response_model=list[SimulationOut])
def list_simulations(investigation_id: uuid.UUID, session: SessionDep) -> list[Simulation]:
    _investigation(session, investigation_id)
    return list(
        session.scalars(
            select(Simulation)
            .where(Simulation.investigation_id == investigation_id)
            .order_by(Simulation.created_at)
        )
    )


@router.get("/simulations/{simulation_id}", response_model=SimulationOut)
def get_simulation(simulation_id: uuid.UUID, session: SessionDep) -> Simulation:
    sim = session.get(Simulation, simulation_id)
    if sim is None:
        raise HTTPException(404, detail="Simulation not found.")
    return sim


# ---------------------------------------------------------------------------- workflow and notes


class StatusChange(BaseModel):
    status: InvestigationStatus
    note: str | None = Field(None, max_length=5000)
    reviewer: str | None = Field(None, max_length=120)


class NoteCreate(BaseModel):
    decision: Decision
    note: str = Field(min_length=1, max_length=5000)
    finding_id: uuid.UUID | None = None
    reviewer: str | None = Field(None, max_length=120)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    finding_id: uuid.UUID | None
    decision: Decision
    note: str
    reviewer: str | None
    reviewed_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    actor: str
    action: str
    occurred_at: datetime
    metadata_json: dict


@router.patch("/investigations/{investigation_id}/status")
def change_status(investigation_id: uuid.UUID, body: StatusChange, session: SessionDep) -> dict:
    inv = _investigation(session, investigation_id)
    try:
        service.change_status(session, inv, body.status, body.note, body.reviewer)
    except service.WorkflowError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {"id": str(inv.id), "status": inv.status.value}


@router.post(
    "/investigations/{investigation_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED
)
def add_note(investigation_id: uuid.UUID, body: NoteCreate, session: SessionDep):
    inv = _investigation(session, investigation_id)
    if not body.note.strip():
        raise HTTPException(422, detail="note must not be blank")
    try:
        return service.add_note(session, inv, body.decision, body.note, body.finding_id, body.reviewer)
    except service.WorkflowError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/investigations/{investigation_id}/notes", response_model=list[NoteOut])
def list_notes(investigation_id: uuid.UUID, session: SessionDep):
    _investigation(session, investigation_id)
    return list(
        session.scalars(
            select(ResolutionNote)
            .where(ResolutionNote.investigation_id == investigation_id)
            .order_by(ResolutionNote.reviewed_at)
        )
    )


@router.get("/investigations/{investigation_id}/history", response_model=list[EventOut])
def investigation_history(investigation_id: uuid.UUID, session: SessionDep):
    return service.history(session, _investigation(session, investigation_id))


# ---------------------------------------------------------------------------- memory


@router.get("/investigations/{investigation_id}/similar")
def similar_investigations(investigation_id: uuid.UUID, session: SessionDep) -> dict:
    inv = _investigation(session, investigation_id)
    return {
        "method": "Rule-based point scoring on shared confirmed/suspected rules, metric definitions, sources and definition differences. Suggestions are never applied automatically.",
        "matches": service.similar(session, inv),
    }


@router.get("/memory/patterns")
def recurring_patterns(session: SessionDep, include_demo: bool = True) -> list[dict]:
    return service.patterns(session, include_demo)
