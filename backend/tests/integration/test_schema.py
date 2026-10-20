import uuid

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.conftest import alembic_config, reset_schema

pytestmark = pytest.mark.db


def test_migrations_round_trip(db_url):
    """Upgrade, downgrade to empty, upgrade again. Runs on its own schema state, then restores head."""
    engine = create_engine(db_url)
    cfg = alembic_config(db_url)
    reset_schema(engine)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}
    command.upgrade(cfg, "head")
    assert "dataset_records" in inspect(engine).get_table_names()
    engine.dispose()


def _insert_upload(conn) -> uuid.UUID:
    upload_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO file_uploads (id, original_filename, sha256, size_bytes, file_type, sheet_names)"
            " VALUES (:id, 'a.csv', :sha, 10, 'csv', '[]')"
        ),
        {"id": upload_id, "sha": uuid.uuid4().hex * 2},
    )
    return upload_id


def test_enum_columns_reject_unknown_values(db_session):
    with pytest.raises(IntegrityError, match="ck_file_uploads_file_type"):
        db_session.execute(
            text(
                "INSERT INTO file_uploads (id, original_filename, sha256, size_bytes, file_type, sheet_names)"
                " VALUES (:id, 'a.json', 'x', 1, 'json', '[]')"
            ),
            {"id": uuid.uuid4()},
        )


def test_query_dataset_requires_connection_and_hash(db_session):
    with pytest.raises(IntegrityError, match="ck_datasets_query_provenance"):
        db_session.execute(
            text(
                "INSERT INTO datasets (id, label, source_type, column_mapping, schema_json, fingerprint,"
                " row_count, rejected_row_count, is_demo)"
                " VALUES (:id, 'q', 'postgres_query', '{}', '{}', 'f1', 0, 0, false)"
            ),
            {"id": uuid.uuid4()},
        )


def test_file_dataset_requires_upload(db_session):
    with pytest.raises(IntegrityError, match="ck_datasets_file_provenance"):
        db_session.execute(
            text(
                "INSERT INTO datasets (id, label, source_type, column_mapping, schema_json, fingerprint,"
                " row_count, rejected_row_count, is_demo)"
                " VALUES (:id, 'f', 'csv', '{}', '{}', 'f2', 0, 0, false)"
            ),
            {"id": uuid.uuid4()},
        )


def test_dataset_fingerprint_is_unique(db_session):
    conn = db_session.connection()
    upload_id = _insert_upload(conn)
    insert = text(
        "INSERT INTO datasets (id, label, source_type, upload_id, column_mapping, schema_json, fingerprint,"
        " row_count, rejected_row_count, is_demo)"
        " VALUES (:id, 'f', 'csv', :upload, '{}', '{}', 'same', 0, 0, false)"
    )
    conn.execute(insert, {"id": uuid.uuid4(), "upload": upload_id})
    with pytest.raises(IntegrityError, match="uq_datasets_fingerprint"):
        conn.execute(insert, {"id": uuid.uuid4(), "upload": upload_id})
