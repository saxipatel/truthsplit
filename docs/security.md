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

## Web server and containers

| Control | Where | Verified by |
|---|---|---|
| The UI is a static production build served by nginx, not the Vite dev server | `frontend/Dockerfile`, `frontend/nginx/` | M8 verification: `/assets/*` immutable, SPA routes fall back to `index.html` |
| Security headers on every response: Content-Security-Policy (`script-src 'self'`, `object-src 'none'`, `base-uri 'none'`, `frame-ancestors 'self'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: same-origin`, `Permissions-Policy` | `frontend/nginx/security-headers.conf` | `curl -I` in the M8 verification |
| `server_tokens off` (no nginx version in headers or error pages) | `default.conf` | same |
| Request body cap of 30 MB at nginx (JSON 413), and the API's own 25 MB streaming cap (JSON 413) behind it | `client_max_body_size`, `TRUTHSPLIT_MAX_UPLOAD_MB` | a 32 MB upload gets nginx's 413, a 26 MB upload gets the API's 413 |
| Containers run as non-root: backend `truthsplit` (uid 1000), nginx (uid 101, unprivileged image listening on 8080) | Dockerfiles | `docker compose exec … id` |

## Dependency and secret scanning

Everything except the image scan runs in CI on every push (`.github/workflows/ci.yml`, `security` job). All of it was run in the M8 verification:

| Check | Tool | M8 result |
|---|---|---|
| Python static analysis | `bandit -r app` | 0 issues |
| Python dependency advisories | `pip-audit` | no known vulnerabilities |
| npm advisories (frontend incl. dev, e2e) | `npm audit` | 0 vulnerabilities, after upgrading react-router to 7.18 and Playwright to 1.56.1 |
| Secrets in Git history | `gitleaks git` | no leaks in any commit |
| Secrets in the working tree | `gitleaks dir` | only the generated passwords in `.env`, which is git-ignored and never committed |
| Container images (fixable HIGH/CRITICAL; manual, not in CI) | Trivy 0.57.1 | backend: 0. frontend (`nginx-unprivileged:1.30-alpine`): 0. `postgres:16-alpine`: 0 in OS packages; 26 in the Go standard library compiled into the upstream `gosu` binary (see below) |

Dependabot (`.github/dependabot.yml`) opens update pull requests for pip, npm, GitHub Actions and Docker base images.

## Known limitations

- **A role can change its own session defaults.** `ALTER ROLE <self> SET default_transaction_read_only = off` succeeds for any role, with no privilege to prevent it. It grants no write access, so the role's privileges still refuse every write (verified: 12 write or escalation attempts failed with 42501 with read-only mode off). TruthSplit cannot issue it either, because every query runs in a read-only transaction, and it reports the role's current default in the connection test.
- **GRANT without privileges.** `GRANT` by a role without grant options returns a warning instead of an error, and grants nothing.
- **Snapshots go stale.** Snapshots are immutable and are not refreshed automatically. Use the freshness check (`POST /api/datasets/{id}/freshness`).
- **Local credentials.** They are only as safe as `.env`. Use `scripts/generate_env.sh` rather than copying `.env.example`.
- **`gosu` in the official PostgreSQL image.** Trivy reports Go standard-library advisories (crypto/tls, crypto/x509, net/url) in the `gosu` binary that the upstream image uses once at start-up to drop root privileges. `gosu` makes no network or TLS connections, so these code paths are not reachable; the image is used as published and Dependabot tracks new releases.

## Not in place

- **Authentication and authorization.** TruthSplit is a single-user local tool. Every port binds to 127.0.0.1; do not expose it on a network without putting an authenticating proxy in front of it.
- **Rate limiting.** Resource-intensive endpoints (imports, investigations, simulations) are bounded by the upload size, row limit and statement timeouts, but not by request rate.
- **TLS.** nginx serves plain HTTP on localhost. A deployment beyond localhost needs TLS termination in front of it.
