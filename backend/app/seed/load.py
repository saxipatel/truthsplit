"""Load the demo scenarios into the metadata database. Safe to run repeatedly.

    docker compose exec backend python -m app.seed.load

Needs the `demo` source connection. Datasets, definitions and investigations it creates are marked
`is_demo` so the UI can label them as synthetic.
"""

import io
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.postgres import get_profile
from app.adapters.registered_queries import REGISTRY, coerce_params
from app.comparison.metrics import MetricSpec
from app.core.config import Settings, get_settings
from app.db.imports import create_dataset, store_upload
from app.db.investigations import InvestigationSide, create_metric_definition, run_investigation
from app.db.session import _session_factory
from app.db.sources import snapshot_query
from app.models import Dataset, Investigation, MetricDefinition
from app.normalization.mapping import ColumnMapping
from app.seed.demo import FILE_BUILDERS, manifest
from app.seed.shop import build_orders


def _metric(session: Session, definition: dict) -> MetricDefinition:
    """Reuse the newest version of the named definition if it already has this spec."""
    spec = MetricSpec.model_validate({k: v for k, v in definition.items() if k != "name"})
    latest = session.scalars(
        select(MetricDefinition)
        .where(MetricDefinition.name == definition["name"])
        .order_by(MetricDefinition.version.desc())
    ).first()
    if latest is not None and MetricSpec.from_model(latest) == spec:
        return latest
    return create_metric_definition(session, definition["name"], "Demo definition (synthetic data).", spec)


def _snapshot(session: Session, settings: Settings, key: str, params: dict) -> Dataset:
    query = REGISTRY[key]
    return snapshot_query(
        session, settings, get_profile(query.connection), query, coerce_params(query, params), is_demo=True
    ).dataset


def _file(session: Session, settings: Settings, name: str, mapping: dict) -> Dataset:
    stored = store_upload(
        session,
        io.BytesIO(FILE_BUILDERS[name](build_orders())),
        name,
        storage_dir=settings.storage_dir,
        max_bytes=settings.max_upload_mb * 1024 * 1024,
    )
    return create_dataset(
        session,
        stored.upload,
        ColumnMapping(**mapping),
        storage_dir=settings.storage_dir,
        max_rows=settings.max_rows,
        is_demo=True,
    ).dataset


def _investigation(
    session: Session, title: str, left: InvestigationSide, right: InvestigationSide
) -> Investigation:
    existing = session.scalar(
        select(Investigation).where(Investigation.title == title, Investigation.is_demo.is_(True))
    )
    return existing or run_investigation(session, title, left, right, is_demo=True)


def load_demo(session: Session, settings: Settings) -> dict:
    out = {}
    for scenario in manifest()["scenarios"]:
        sides = []
        for spec in (scenario["left"], scenario["right"]):
            dataset = (
                _snapshot(session, settings, spec["query"], spec["params"])
                if "query" in spec
                else _file(session, settings, spec["file"], spec["mapping"])
            )
            sides.append(InvestigationSide(dataset, _metric(session, spec["metric"])))
        inv = _investigation(session, scenario["title"], *sides)
        out[scenario["id"]] = {
            "investigation_id": str(inv.id),
            "title": inv.title,
            "delta": str(inv.baseline_delta),
            "unexplained": str(inv.unexplained_delta),
        }
    return out


if __name__ == "__main__":
    with _session_factory()() as session:
        json.dump(load_demo(session, get_settings()), sys.stdout, indent=2)
        print()
