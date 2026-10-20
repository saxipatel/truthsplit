import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

TEST_DATABASE_URL = os.environ.get("TRUTHSPLIT_TEST_DATABASE_URL")
TEST_SOURCE_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_URL")

# The app requires a database URL at import time. Tests that touch a database inject their own session,
# so without a test database this only needs to be syntactically valid.
os.environ.setdefault(
    "TRUTHSPLIT_DATABASE_URL", TEST_DATABASE_URL or "postgresql+psycopg://unset@localhost/unset"
)


def alembic_config(url: str) -> Config:
    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    cfg.attributes["database_url"] = url
    return cfg


def reset_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))


@pytest.fixture(scope="session")
def db_url() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TRUTHSPLIT_TEST_DATABASE_URL is not set")
    return TEST_DATABASE_URL


@pytest.fixture(scope="session")
def migrated_engine(db_url: str) -> Iterator[Engine]:
    engine = create_engine(db_url)
    reset_schema(engine)
    command.upgrade(alembic_config(db_url), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after each test."""
    with migrated_engine.connect() as conn:
        trans = conn.begin()
        session = sessionmaker(bind=conn, join_transaction_mode="create_savepoint")()
        yield session
        session.close()
        trans.rollback()
