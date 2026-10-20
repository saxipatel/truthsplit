"""The demo source database's reader role must be unable to write, whatever SQL it is sent."""

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

SOURCE_URL = os.environ.get("TRUTHSPLIT_TEST_SOURCE_URL")
pytestmark = pytest.mark.skipif(not SOURCE_URL, reason="TRUTHSPLIT_TEST_SOURCE_URL is not set")


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE ts_probe (id int)",
        "CREATE TEMP TABLE ts_probe (id int)",
        "SET TRANSACTION READ WRITE; CREATE TABLE ts_probe (id int)",
    ],
)
def test_reader_role_cannot_write(statement):
    engine = create_engine(SOURCE_URL)
    with engine.connect() as conn, pytest.raises(DBAPIError):
        conn.execute(text(statement))
    engine.dispose()


def test_reader_sessions_default_to_read_only():
    engine = create_engine(SOURCE_URL)
    with engine.connect() as conn:
        assert conn.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
        assert conn.execute(text("SELECT current_setting('statement_timeout')")).scalar() == "5s"
    engine.dispose()
