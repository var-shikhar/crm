# Activity log: planning to verification

Built by Claude Code (model Claude Fable 5.2) working in the `crm-system` folder, in two stretches:
the first build on 14 November 2026 (IST, started 22:28; sections 1 to 8) and follow-up rounds on
16 November 2026 on Windows (sections 9 to 12). Steps are in the order they happened. Step numbers
are the order of work, so the documentation steps 16 and 17 appear in section 8.
Only two clock times were captured on the first day (22:28 start, 22:47 at documentation), so
individual steps are not timed.

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
| Unit/API tests | `pytest` per service | 18 auth + 9 customer + 5 audit = **32 passed** (37 after section 7, 41 after section 9, 45 after section 11, 48 after section 12) |
| End-to-end, real processes over HTTP | `scripts/smoke_test.py` | **25 of 25 checks passed** (32 after section 7, 39 after section 9, 41 after section 11, 47 after section 12) |
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

At the end of the first day nothing had been committed to git. The first commit was made on
16 November (section 10).

## 9. Third round: fixes requested after trying the system (16 November 2026, on Windows)

| # | Change | What was done | Verification |
|---|---|---|---|
| 21 | **Delete a customer** | New `DELETE /customers/{id}` behind a new permission `customer:delete` (in `admin` only by default). Refused for a blocked customer. The activity log keeps a copy of the record. UI: "Delete customer" on the customer page. | 1 new test; 3 new end-to-end checks |
| 22 | **Delete a user** | New `DELETE /users/{id}` (`user:manage`). Refused for your own account and for the last active administrator. Signs the user out everywhere. The bootstrap administrator is now created only while no administrator exists, so a deleted one does not return at restart. UI: "Delete user" in the Manage panel. | 2 new tests; 3 new end-to-end checks |
| 23 | **Form validation** | Shared rules in `shared/crm_common/validation.py` for names, places, phone, postal code and dates, used by the customer, employment, contact and user schemas; matching rules on the UI inputs; clearer error text. | 1 new test, 1 extended; 1 new end-to-end check |
| 24 | **"Change password" reported as failing** | Not reproduced: the endpoint works for the bootstrap admin, after authenticator enrolment, and for created and admin-reset users. No code change. | In-process reproduction script |

Totals after this round: **41 tests**, **39 end-to-end checks**, all passing. The browser UI was syntax-checked
and its input patterns tested in Node, but not clicked through in a browser.

## 10. Repository and web UI polish (16 November 2026)

| # | Activity | Outcome |
|---|---|---|
| 25 | First commits. | The build so far was committed, then the third round as its own commit. |
| 26 | Removed paths of the local machine from the documents. | `ACTIVITY_LOG.md` and `PROMPTS.md` no longer name local folders. |
| 27 | Web UI for phones and accessibility. | Layout for screens under 640px, sticky header, styling for invalid fields, skip link, `aria-current`, labelled action columns, a title per page, favicon, colour scheme, `noscript` text, print layout. No server change. |

## 11. Fourth round: sign-in without a code by permission, and a documentation review (16 November 2026)

Requested by the user: administrators should sign in without a code, better done as a permission
that lets any account do so; and a check of the documentation against the brief.

| # | Change | What was done | Verification |
|---|---|---|---|
| 28 | **Permission `auth:mfa:skip`** | New permission in the catalogue. An account holding it through any role gets its tokens from the password step, whatever second factor it has. `admin` holds every permission, so administrators now sign in with email and password only. The per-user "MFA optional" setting from section 7 is unchanged. Users and "my account" responses gain `mfa_skipped`. The sign-in event records `mfa_skipped_by`. UI: "no code asked" in the users list, a notice on "My security". | 4 new tests, 5 moved from the administrator to an ordinary user; 2 new end-to-end checks |
| 29 | **Documentation review against the brief** | Findings and fixes below. | Links and figures checked against the code |

Documentation findings:

| Finding | Fix |
|---|---|
| The brief asks for documentation "for all microservice you build". The documents were split by topic only (architecture, API, deployment), so no single page described one service. | Added `docs/services/auth-service.md`, `customer-service.md` and `audit-service.md`. |
| Nothing mapped the brief to the place where each point is met. | Added that table, and a one-line purpose for each document, to the README. |
| This log said nothing was committed, numbered a section "7a" after 7, and its header gave one date. | Corrected. |
| The prompt log ended at the second round. | Added the third and fourth rounds. The wording of the third-round prompts was not kept; that is stated there. |
| The README gave old test counts and said the end-to-end test needs a fresh database. | Corrected: 45 tests, 41 checks, no such condition. |
| `run-local.sh` does not run on Windows without WSL. | Stated in the README and DEPLOYMENT.md with the manual route. The script itself is unchanged. |

Issues met in this round:

| Issue | Resolution |
|---|---|
| Windows application control refused to load SQLAlchemy's compiled extensions in a fresh virtualenv. | Installed SQLAlchemy as pure Python (`--no-binary sqlalchemy`). No code change. |
| The end-to-end test could not use the stack already running on ports 8001 to 8080 without adding test records to its database, and that stack ran the old code. | Started a second copy on ports 8101 to 8180 with its own throwaway data directory, ran the test against it, stopped it. `smoke_test.py` gained `CRM_LOCAL_DIR` for this. |
| OpenAPI files exported from that copy carried a wrong server URL, because Git Bash rewrote the `--root-path` argument. | Corrected the three `servers.url` values back to `/auth`, `/crm`, `/audit`. |

Totals after this round: **45 tests** (28 auth, 11 customer, 6 audit) and **41 end-to-end checks**, all
passing. The browser UI was syntax-checked, not clicked through. The container stack is still not run.

## 12. Fifth round: who sees users and activity (16 November 2026)

Requested by the user: the list of users should be visible to administrators only, and the activity
log should show each user the activity of their own account, with administrators seeing everything.

| # | Change | What was done | Verification |
|---|---|---|---|
| 30 | **Users visible to administrators only** | `user:read` taken out of the default `manager` role, so only `admin` holds it. The permission still exists and can be granted on purpose. The "Users" page already appears only for holders. | 1 new test; 1 new end-to-end check |
| 31 | **Activity log per account** | `audit:read` now means "everyone's activity" and was taken out of `manager`. Reading the log is open to every signed-in user, limited in the database query to events they caused and events on their own user record. Applies to search, single event (404 outside the view) and CSV export. Reads are logged with `scope: all` or `own`. `authz/check` accepts a call without a permission, which only authenticates the token. UI: "Activity" is shown to everyone, titled "My activity" without `audit:read`, and the filter by user email is shown to administrators only. | 2 new tests, 1 adjusted; 6 new end-to-end checks, 1 replaced |
| 32 | **Existing local database** | Default roles are seeded once, so the `manager` role in the running local database was edited through the API (logged as `role.updated`), and the auth and audit services were restarted with the new code. | Checked through the gateway |

Decisions made without asking: "admin" was read as "holds `audit:read` / `user:read`", so the rule
stays a permission and not a role name; "own account" includes what others did to the account, not
only what its user did.

Totals after this round: **48 tests** (30 auth, 11 customer, 7 audit) and **47 end-to-end checks**, all
passing against a second copy of the stack on spare ports. The browser UI was syntax-checked, not
clicked through.

## 13. Documentation tightened and pushed (16 November 2026)

| # | Activity | Outcome |
|---|---|---|
| 33 | Shortened the README and the sign-in and authorization parts of ARCHITECTURE.md. | README runs in the order a reader needs: what it is, run, test, roles, documents, brief. No facts removed; long bullet lists became one table each. |
| 34 | Committed rounds four and five and pushed to `origin/main`. | See the git history. |
| 35 | Checked for dead code: pyflakes and vulture on the Python code, and a script over the web UI for unused functions, handlers and CSS classes. | One unused constant removed from `services/auth-service/app/seed.py`. Nothing else found. |
| 36 | Wrote `docs/GETTING_STARTED.md` and linked it from the README. | One page for starting the project: both ways to run it, first sign-in, checks, troubleshooting. Every Markdown file is now linked from the README. |
