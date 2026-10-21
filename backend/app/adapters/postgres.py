"""Read-only access to configured PostgreSQL source databases.

Layers, outermost first:
  1. Only registered queries run (see registered_queries.py); users supply parameter values, never SQL.
  2. The destination host must be in TRUTHSPLIT_ALLOWED_SOURCE_HOSTS, checked before connecting.
  3. Before every query the login role's privileges are inspected, and the query is refused if the role
     is a superuser, can create roles or databases, can create objects, or can write to any table.
  4. The query runs inside SET TRANSACTION READ ONLY with statement and lock timeouts, wrapped so that
     exactly one SELECT executes, and is always rolled back.
  5. The database role itself should grant nothing but SELECT; that is the guarantee, the rest is defence.

Connection URLs never appear in logs, errors or API responses.
"""

import logging
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.pool import NullPool

from app.adapters.registered_queries import RegisteredQuery
from app.core.config import Settings

log = logging.getLogger(__name__)

_PROFILE_ENV = re.compile(r"^TRUTHSPLIT_SOURCE_([A-Z0-9_]+)_URL$")
CONNECT_TIMEOUT_S = 5


class SourceError(Exception):
    """A failure safe to show to users: no URLs, credentials or server internals."""


class QueryTooLargeError(SourceError):
    pass


def source_profiles(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Profile name -> URL, from TRUTHSPLIT_SOURCE_<NAME>_URL environment variables."""
    env = os.environ if environ is None else environ
    return {m.group(1).lower(): url for key, url in env.items() if (m := _PROFILE_ENV.match(key)) and url}


@dataclass(frozen=True)
class Profile:
    name: str
    url: str

    def describe(self) -> dict[str, Any]:
        try:
            u = make_url(self.url)
        except ArgumentError:
            return {"name": self.name, "host": None, "port": None, "database": None}
        return {"name": self.name, "host": u.host, "port": u.port or 5432, "database": u.database}


def get_profile(name: str, environ: dict[str, str] | None = None) -> Profile:
    url = source_profiles(environ).get(name)
    if url is None:
        raise SourceError(f"No source connection named {name!r} is configured.")
    return Profile(name, url)


def _check_host(profile: Profile, settings: Settings) -> None:
    try:
        host = (make_url(profile.url).host or "").lower()
    except ArgumentError:
        raise SourceError(f"Source {profile.name!r} has an invalid connection URL.") from None
    if host not in settings.allowed_source_host_set:
        raise SourceError(f"Host for source {profile.name!r} is not in the allowed source hosts.")


@contextmanager
def read_only_connection(profile: Profile, settings: Settings) -> Iterator[Connection]:
    _check_host(profile, settings)
    timeout = int(settings.statement_timeout_ms)
    engine = create_engine(
        profile.url,
        poolclass=NullPool,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_S,
            "application_name": "truthsplit",
            "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout}",
        },
    )
    try:
        try:
            conn = engine.connect()
        except DBAPIError as exc:
            raise SourceError(
                f"Could not connect to source {profile.name!r} ({_sqlstate(exc) or 'connection failed'})."
            ) from None
        try:
            trans = conn.begin()
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            conn.exec_driver_sql(f"SET LOCAL statement_timeout = {timeout}")
            conn.exec_driver_sql(f"SET LOCAL lock_timeout = {min(timeout, 2000)}")
            yield conn
        finally:
            trans.rollback()
            conn.close()
    finally:
        engine.dispose()


@dataclass
class PrivilegeReport:
    user: str
    server_version: str
    # Informational: whether the role itself defaults to read-only sessions. Not required, because the
    # privilege checks below already reject any role that could write.
    read_only_default: bool
    problems: list[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return not self.problems


_PRIVILEGE_SQL = """
SELECT current_user AS user_name,
       current_setting('server_version') AS server_version,
       -- The role's own default, not this session's (which TruthSplit always forces to read-only).
       COALESCE('default_transaction_read_only=on' = ANY(r.rolconfig), false) AS read_only_default,
       r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolbypassrls,
       (SELECT count(*) FROM pg_namespace n
         WHERE n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
           AND has_schema_privilege(current_user, n.oid, 'CREATE')) AS create_schemas,
       has_database_privilege(current_user, current_database(), 'CREATE') AS create_db_objects,
       has_database_privilege(current_user, current_database(), 'TEMP') AS temp_tables,
       (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND n.nspname NOT LIKE 'pg\\_%' AND n.nspname <> 'information_schema'
           AND (has_table_privilege(current_user, c.oid, 'INSERT') OR has_table_privilege(current_user, c.oid, 'UPDATE')
                OR has_table_privilege(current_user, c.oid, 'DELETE') OR has_table_privilege(current_user, c.oid, 'TRUNCATE'))
       ) AS writable_tables
FROM pg_roles r WHERE r.rolname = current_user
"""


def inspect_privileges(conn: Connection) -> PrivilegeReport:
    row = conn.execute(text(_PRIVILEGE_SQL)).mappings().one()
    report = PrivilegeReport(row["user_name"], row["server_version"], row["read_only_default"])
    checks = [
        (row["rolsuper"], "role is a superuser"),
        (row["rolcreaterole"], "role can create roles"),
        (row["rolcreatedb"], "role can create databases"),
        (row["rolbypassrls"], "role bypasses row-level security"),
        (row["create_schemas"], f"role can create objects in {row['create_schemas']} schema(s)"),
        (row["create_db_objects"], "role can create schemas in the database"),
        (row["temp_tables"], "role can create temporary tables"),
        (row["writable_tables"], f"role can write to {row['writable_tables']} table(s)"),
    ]
    report.problems = [message for failed, message in checks if failed]
    return report


def test_connection(profile: Profile, settings: Settings) -> PrivilegeReport:
    with read_only_connection(profile, settings) as conn:
        return inspect_privileges(conn)


def _require_safe(conn: Connection, profile: Profile) -> PrivilegeReport:
    report = inspect_privileges(conn)
    if not report.safe:
        log.warning(
            "source refused: unsafe role", extra={"source": profile.name, "problems": report.problems}
        )
        raise SourceError(
            f"Source {profile.name!r} is not a read-only login and will not be queried: {'; '.join(report.problems)}."
        )
    return report


def catalog(profile: Profile, settings: Settings) -> list[dict]:
    """Tables and columns the role may SELECT from, outside system schemas."""
    sql = text(
        """
        SELECT c.table_schema, c.table_name, c.column_name, c.data_type
        FROM information_schema.columns c
        WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND has_table_privilege(current_user, quote_ident(c.table_schema) || '.' || quote_ident(c.table_name), 'SELECT')
        ORDER BY c.table_schema, c.table_name, c.ordinal_position
        """
    )
    with read_only_connection(profile, settings) as conn:
        _require_safe(conn, profile)
        tables: dict[tuple[str, str], list[dict]] = {}
        for schema, table, column, dtype in conn.execute(sql):
            tables.setdefault((schema, table), []).append({"name": column, "type": dtype})
    return [{"schema": s, "table": t, "columns": cols} for (s, t), cols in tables.items()]


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    server_version: str
    role: str


def run_registered_query(
    profile: Profile,
    settings: Settings,
    query: RegisteredQuery,
    params: dict,
    *,
    max_rows: int,
    preview: bool = False,
) -> QueryResult:
    """Run one registered query. Results are ordered by every column so a snapshot is reproducible.
    More than `max_rows` rows is an error unless `preview` is set, in which case only the first rows return."""
    # Wrapping the registered SQL as a subquery means exactly one SELECT statement executes. Ordering by the
    # row value sorts on every column in turn, so the same data always yields the same snapshot.
    sql = f"SELECT * FROM ({query.sql}) AS registered_query ORDER BY registered_query LIMIT :__limit"  # nosec B608
    wrapped = text(sql)
    limit = max_rows if preview else max_rows + 1
    with read_only_connection(profile, settings) as conn:
        report = _require_safe(conn, profile)
        try:
            result = conn.execute(wrapped, {**params, "__limit": limit})
        except DBAPIError as exc:
            raise SourceError(_query_error(exc, settings)) from None
        columns = list(result.keys())
        rows = [tuple(r) for r in result]
    if not preview and len(rows) > max_rows:
        raise QueryTooLargeError(f"Query returned more than {max_rows:,} rows, which is the import limit.")
    log.info("registered query run", extra={"source": profile.name, "query": query.key, "rows": len(rows)})
    return QueryResult(columns, rows, report.server_version, report.user)


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


def _query_error(exc: DBAPIError, settings: Settings) -> str:
    state = _sqlstate(exc)
    if state == "57014":
        return f"Query cancelled after the {settings.statement_timeout_ms} ms statement timeout."
    if state == "25006":
        return "Query attempted to write and was refused by the read-only transaction."
    if state == "42501":
        return "Permission denied on the source database."
    if state == "55P03":
        return "Query timed out waiting for a lock."
    return f"Source query failed (SQLSTATE {state or 'unknown'})."
