# Security controls

The threat model is a local tool that is given credentials for a reporting database and must never be able to change it. The tool also accepts files from users, and must not be tricked into reading or contacting things it shouldn't.

## Source databases

| Control | Where | Verified by |
|---|---|---|
| Only registered SQL runs; users supply typed parameter values, bound, never spliced | `adapters/registered_queries.py`, `coerce_params` | `test_parameter_validation` (including `'; DROP TABLE` as a date) |
| Destination host must be in `TRUTHSPLIT_ALLOWED_SOURCE_HOSTS`, checked before connecting | `postgres._check_host` | `test_disallowed_host_is_refused_before_connecting` |
| Role privileges inspected before every query: refused if superuser, CREATEROLE, CREATEDB, BYPASSRLS, CREATE on any schema or the database, TEMP, or INSERT/UPDATE/DELETE/TRUNCATE on any table | `postgres.inspect_privileges` | `test_privileged_role_is_reported_and_never_queried` (uses the compose superuser) |
| `SET TRANSACTION READ ONLY`, always rolled back; session `default_transaction_read_only=on` | `postgres.read_only_connection` | `test_registered_sql_cannot_write_even_if_it_tries` (`FOR UPDATE`, data-modifying CTE, second statement) |
| Statement timeout (`TRUTHSPLIT_STATEMENT_TIMEOUT_MS`), lock timeout, 5 s connect timeout | same | `test_statement_timeout` |
| Registered SQL wrapped as a subquery, so exactly one SELECT executes | `run_registered_query` | as above |
| Result row limit; exceeding it is an error, never a silent truncation | same | `test_row_limit_is_an_error` |
| The demo reader role has SELECT only: no CREATE on `public`, no TEMP, and defaults to read-only | `docker/source-db/01-reader-role.sh` | `test_source_role.py` (writes fail even after `SET TRANSACTION READ WRITE`) |
| Credentials only in environment variables; the database stores a profile name; errors carry SQLSTATE codes, not server messages | `get_profile`, `_query_error` | `test_connection_listing_never_exposes_credentials`, `test_bad_credentials_give_a_sanitised_error` |

The database role is the real guarantee; everything else is defence in depth. Do not configure a source with a role that can write. TruthSplit will refuse to query it, but the credentials would still be present in the environment.

## Files

Extension allowlist with signature checks, a streaming size cap, filename sanitization, a row limit, XLSX parsed through defusedxml, and refusal of macro-enabled and oversized archives. See [metric-contract.md](metric-contract.md).

## Application database and logs

- **Generated SQL:** metric proof SQL (`comparison/sql.py`) takes identifiers only from a fixed allowlist, and every value is a bind parameter. `test_injection_shaped_attribute_names_and_values_are_bound_not_spliced` covers this.
- **Ports:** all services bind to 127.0.0.1.
- **Logs:** JSON, and never include connection URLs or passwords. The verification runs grep the logs for this.

## Known limitations

- **A role can change its own session defaults.** `ALTER ROLE <self> SET default_transaction_read_only = off` succeeds for any role, with no privilege to prevent it. It grants no write access, so the role's privileges still refuse every write (verified: 12 write or escalation attempts failed with 42501 with read-only mode off). TruthSplit cannot issue it either, because every query runs in a read-only transaction, and it reports the role's current default in the connection test.
- **GRANT without privileges.** `GRANT` by a role without grant options returns a warning instead of an error, and grants nothing.
- **Snapshots go stale.** Snapshots are immutable and are not refreshed automatically. Use the freshness check (`POST /api/datasets/{id}/freshness`).
- **Local credentials.** They are only as safe as `.env`. Use `scripts/generate_env.sh` rather than copying `.env.example`.

## Not yet in place

- Rate limiting of resource-intensive endpoints (planned for Milestone 8).
- HTML report sanitization (reports are not built yet).
- Authentication: TruthSplit is a single-user local tool and has none.
