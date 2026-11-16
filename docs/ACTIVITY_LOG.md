# Activity log: planning to verification

Session date: 14 November 2026 (IST). Work started 22:28. Built by Claude Code (model Claude Fable 5.2)
working in `/Users/himanshu/Desktop/Langraph/crm-system`. Steps are in the order they happened.
Only two clock times were captured (22:28 start, 22:47 at documentation), so individual steps are not timed.

## 1. Planning

| # | Activity | Outcome |
|---|---|---|
| 1 | Read the brief (4 requirements + 3 records to keep). | Scope fixed: auth, customer, audit services; gateway; UI; docs. |
| 2 | Inspected the workspace and tooling. | Existing folder is an unrelated Python scraper project, so the CRM went into its own `crm-system/` folder. Found Python 3.13, uv, Node 24, Docker CLI (daemon not running). |
| 3 | Chose the stack. | FastAPI + SQLAlchemy (matches the Python environment already present), PostgreSQL for containers, SQLite for local and tests. |
| 4 | Design decisions (details in ARCHITECTURE.md). | (a) Auth service is an OAuth 2.0 server; login is password grant then a custom MFA grant. (b) MFA is mandatory: email code by default, authenticator app optional. (c) Other services hold no auth logic and call `authz/check` per request. (d) Activity log is its own service, fed by a transactional outbox. (e) A blocked customer is read-only. |

Decisions made without asking, because the brief left them open: the stack, mandatory (rather than
optional) MFA, a separate audit service, the four default roles, and treating "blocked" as read-only.

## 2. Build

| # | Activity | Outcome |
|---|---|---|
| 5 | Shared library `shared/crm_common`. | Authorization client, audit outbox + background shipper, UTC datetime type, JSON logging, request-id middleware. |
| 6 | Auth service. | Models (users, roles, permissions, OAuth clients, refresh tokens, MFA challenges, revoked tokens); Argon2 passwords; RS256 keys + JWKS; TOTP with replay protection; token, revoke, introspect, metadata endpoints; `authz/check`; self-service MFA and password; user and role administration; idempotent seed (permissions, roles, clients, first admin). |
| 7 | Audit service. | Idempotent batch ingest, filtered search, single event; no update/delete; viewing the log is logged. |
| 8 | Customer service. | Customers (search, add, edit, block, unblock); employment and related contacts (list, add, edit, remove); field-level change capture for the log. |
| 9 | Tests, one suite per service. | 32 tests. All passed on first run. |

## 3. Containerization

| # | Activity | Outcome |
|---|---|---|
| 10 | Dockerfiles for the three services and the gateway; `docker-compose.yml`; nginx config; Postgres bootstrap script; `.env.example` and `scripts/init-env.sh`. | Non-root, read-only containers; one DB + login per service; only the gateway published. `docker compose config` validates. |
| 11 | Launched Docker Desktop and built the three service images in the background. | Build succeeded. |
| 12 | **Direction change from the user:** do not use Docker or build images; run everything locally. | Stopped all container work. No container was ever started. Container files kept as configuration only. The images from step 11 were left in place. |

## 4. Local run

| # | Activity | Outcome |
|---|---|---|
| 13 | `scripts/dev_gateway.py` (local stand-in for nginx) and `scripts/run-local.sh` (start/stop/status/logs/code/reset). | Four local processes on ports 8001, 8002, 8003, 8080 with SQLite files under `.local/`. |
| 14 | First local start. | Services came up; two defects found in my own scripts (below). |

Defects found and fixed during verification:

| Defect | Cause | Fix |
|---|---|---|
| Local gateway returned 500 for proxied routes. | Route handlers were lambdas returning coroutines; the framework did not treat them as async. | Replaced with proper async handler factory. |
| `run-local.sh stop` did not stop the services. | The recorded PID was a wrapper subshell, not the Python process. | Changed how the process is launched so the PID file holds the real PID. Killed the four orphaned processes by PID. |
| Start command hung when its output was piped. | Background processes inherited the terminal's stdin. | Redirect stdin from `/dev/null`. |
| Smoke test failed one check ("denied attempts are logged"). | Test bug, not a system bug: audit delivery is asynchronous (about 2 s) and that query did not wait. | Added a polling helper for all activity-log checks. |
| `root_path` deprecation warning from FastAPI at startup. | Passing the gateway prefix to the app constructor is deprecated. | Moved it to the server option (`uvicorn --root-path`), in both the local script and the Dockerfiles. |

## 5. Web UI

| # | Activity | Outcome |
|---|---|---|
| 15 | `web/` (HTML, CSS, JS; no dependencies). | Sign-in with MFA step; customers (search, add, edit, block/unblock); employment and contacts; activity log with filters; users; roles; MFA and password settings. Controls appear only for permissions the user holds. |

## 6. Verification

| Check | How | Result |
|---|---|---|
| Unit/API tests | `pytest` per service | 18 auth + 9 customer + 5 audit = **32 passed** (37 after section 7) |
| End-to-end, real processes over HTTP | `scripts/smoke_test.py` | **25 of 25 checks passed** (32 after section 7) |
| UI in a real browser | Headless Chrome driven over its debugging protocol (throwaway profile): sign-in, wrong password, MFA code, add customer, add employment and contact, block, unblock, activity log, users, roles, QR code for authenticator, sign-out | **15 of 15 steps passed**; no script errors or CSP violations |
| Container config | `docker compose config` | Valid. Stack **not run** (see step 12). |

Not verified: the container deployment end to end; real SMTP delivery (local runs print the code to
the log); behaviour on PostgreSQL (tests and local runs use SQLite); the authenticator flow with a
physical phone (tested with generated TOTP codes in the test suite); load or concurrency.

## 7. Second round: changes chosen by the user (from about 22:55)

The agent offered four possible next changes; the user chose three.

| # | Change | What was done | Verification |
|---|---|---|---|
| 18 | **MFA optional per user** | New `users.mfa_required` flag (default true) set by an administrator at creation or later. Password grant returns tokens directly for an exempt account with no second factor. New `POST /me/mfa/disable` (refused while MFA is required). Requiring MFA again switches the account to email codes. Administrators cannot exempt themselves. UI: checkbox on user create/manage, status in the users list, options on "My security". | 4 new tests; 5 new end-to-end checks; 2 new browser steps |
| 19 | **Export the activity log** | New `GET /events/export`: CSV with the same filters as search, 50,000-row cap with a truncation header, formula-injection guard, and the export itself logged. Search and export now share one filter function. UI: "Download CSV" button on the activity log. | 1 new test; 2 new end-to-end checks; 1 new browser step |
| 20 | **Review a service with the user** | Started after 18 and 19; see PROMPTS.md. | |

Issues met in this round:

| Issue | Resolution |
|---|---|
| The new column did not exist in the already-created local database, because tables are only created at startup and there are no migrations. | Backed up `.local/auth.db`, ran `ALTER TABLE users ADD COLUMN mfa_required BOOLEAN NOT NULL DEFAULT 1`, restarted. Existing users kept MFA required. |
| First version of the column default (`DEFAULT 1`) would have been rejected by PostgreSQL for a boolean. | Caught on review before any PostgreSQL run; changed to a portable `true` default. |

Totals after this round: **37 tests**, **32 end-to-end checks**, **18 browser steps**, all passing (23:08).

## 8. Documentation

| # | Activity | Outcome |
|---|---|---|
| 16 | `scripts/export_openapi.py`. | OpenAPI JSON per service and a generated endpoint index, taken from the running services. |
| 17 | README, ARCHITECTURE, API, DEPLOYMENT, this log, PROMPTS. | In `docs/`. |

Nothing was committed to git.
