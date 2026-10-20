import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.main import app


def client_with(session_factory) -> TestClient:
    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    return TestClient(app)


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_live_needs_no_database():
    assert TestClient(app).get("/health/live").json() == {"status": "ok"}


def test_ready_reports_schema_revision(db_session: Session):
    client = client_with(lambda: db_session)
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "database": "ok", "schema_revision": "0001"}


def test_ready_is_503_when_database_unreachable():
    engine = create_engine(
        "postgresql+psycopg://nobody:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    client = client_with(lambda: Session(engine))
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["database"] == "unreachable"
