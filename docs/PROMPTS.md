# Prompt and interaction log

The CRM in this repository was built in one working session with a coding agent (Claude Code, model
Claude Fable 5.2) on 14 November 2026. This file records the prompts that drove the build and what
the agent did in response, phase by phase. User prompts are quoted as written; the agent's side is
summarised, with its own progress messages quoted where they mark a step.

The complete transcript, with every tool call and its output, is kept by Claude Code at
`~/.claude/projects/-Users-himanshu-Desktop-Langraph/649434f8-2ace-4d94-a8fa-09d363404404.jsonl`.

---

## Prompt 1: the brief

> Create a basic CRM system with following features
>
> 1. Auth Service: User and role management for RBAC as a microservices using oAuth system. The auth should support multi factor authentication - email or Google authenticator along with email, password. All other module will use this centralized service to authenticate and authorize user for any action.
>
> 2. Customer management:
>
> User with proper access control can add, edit, block/unblock a customer record. Specific user can add additional records such as employment, related contacts.
>
> 3. All the activity should be logged with user details and timestamp.
>
> 4. System should be configured for containerized deployment.
>
> Along with the final workin system record following
>
> - All the activity you take from planing to deployment
>
> - Keep record of all your prompts and interaction with the coding agent.
>
> - Produce system architecture, API specification and documententation for all microservice you build.

The user asked for the task to be completed together with these records.

### Phase 1: planning

The agent inspected the workspace and tooling (Python 3.13, uv, Node 24, Docker CLI) and fixed the
design before writing code. The brief left several things open; the agent chose rather than asked:

| Open point | Decision |
|---|---|
| Stack | Python, FastAPI, SQLAlchemy; PostgreSQL in containers, SQLite locally and in tests |
| Service split | auth-service, customer-service, and a separate audit-service for the activity log, behind a gateway |
| "oAuth system" | Auth service is an OAuth 2.0 server: password grant, then a second grant that completes sign-in with the one-time code; refresh tokens; client credentials for services |
| MFA | Mandatory. Emailed code by default; authenticator app (Google Authenticator) once enrolled |
| "Centralized" authorization | Other services hold no auth logic; each request asks the auth service to validate the token and decide one permission |
| Activity logging | Events written in the same transaction as the change, delivered to the audit service in the background |
| Roles | admin, manager, agent, viewer by default; administrators can define more |
| Block | A blocked customer is read-only until unblocked |

### Phase 2: shared library and auth service

> "Skeleton is in place under `crm-system/` and Docker Desktop is starting; now writing the shared library (authz client, audit outbox, logging) that all three services use."

> "Shared library done. Now the auth service: config, models, crypto/token helpers."

> "Now the OAuth endpoints (token, MFA grant, refresh rotation, revoke, introspect, JWKS)."

Built: users, roles, permissions and OAuth clients; Argon2 password hashing; RS256 signing keys with
JWKS; emailed codes and TOTP with replay protection; login lockout; refresh-token rotation with reuse
detection; the `authz/check` decision endpoint; self-service MFA and password; user and role
administration; start-up seeding of permissions, roles, clients and the first administrator.

### Phase 3: audit and customer services

> "Auth service is written. Now the audit and customer services."

Built: the append-only activity log (idempotent ingest, filtered search); customers (search, add,
edit, block, unblock); employment records and related contacts, each behind its own permission;
field-level change capture for the log.

### Phase 4: tests

> "All three services are written. Now setting up a test environment and writing tests before containerizing."

> "All 32 tests pass across the three services (18 auth, 9 customer, 5 audit). Now containerization: Dockerfiles, compose, gateway, and env setup."

### Phase 5: containerization

Wrote a Dockerfile per service and for the gateway, `docker-compose.yml`, the nginx configuration,
the PostgreSQL bootstrap script, `.env.example` and a script that generates secrets. The agent also
started Docker Desktop and built the three service images.

> "Images are building in the background. Meanwhile, writing the web UI (login with MFA step, customers, employment/contacts, activity log, user/role admin, MFA settings)."

---

## Prompt 2: run locally, not in Docker

> dont run it in docker dont build images all have to be run locally it is just testing  for self analysis

### Phase 6: local run

The agent stopped container work. No container had been started; the image builds had already
finished and were left in place. The container files were kept as configuration, as the brief asks.

It then wrote a local stand-in for the gateway and a run script, and started the system as four
local processes.

> "Services start fine locally; the local gateway has a routing bug (lambda not treated as async). Fixing that and checking a startup warning."

Three defects in the new scripts were found and fixed here: gateway routing, the PID recorded for
each process, and a hang when the start command's output was piped.

### Phase 7: end-to-end test

> "All four local processes are up and routing correctly through the local gateway. Now writing an end-to-end smoke test that drives the real running system (login with MFA, RBAC, customer lifecycle, audit trail)."

> "One check failed only on timing: audit delivery is asynchronous (outbox, ~2s) and that query didn't wait. Adding a polling helper for all log checks."

> "End-to-end: all 25 checks pass against the locally running system (the gateway errors in the log are stale, from before the routing fix). Now building the web UI on top of the working APIs."

### Phase 8: web UI

> "UI is written. Checking it parses and renders, and fixing the `root_path` deprecation warning seen in the service logs."

> "Backend all green after the change. Now driving the UI in a headless browser to confirm it really works end to end (sign-in, MFA, add customer, block), rather than only checking syntax."

> "The UI works end to end in a real browser (15 steps pass; the two "problems" listed are just the expected 400/403 responses from the wrong-password and MFA-required steps). Now writing the required records: architecture, API spec, activity log, and prompt log."

### Phase 9: documentation

Generated the OpenAPI file for each service and an endpoint index from the running services, and
wrote the README, architecture, API guide, deployment guide and activity log. Restarted the system
and re-ran everything: 32 tests, 25 end-to-end checks.

---

## Prompt 3: second round of changes

With the first version running, the agent listed four changes it could make next: make MFA optional
per user, add password reset by email, export the activity log, or review one service together.
The user chose three:

> Make MFA optional per user, Export the activity log, Review a service with me

### Phase 10: MFA optional per user

> "You picked three: optional MFA per user, activity-log export, and a service review. I'll build the first two now and then start the review with you."

The original design made a second factor mandatory for everyone. This change puts the decision with
the administrator, per user. Decisions taken while building it:

| Question | Decision |
|---|---|
| Default | Still required. Optional is an explicit choice per account. |
| Account created as optional | Starts with no second factor: password only. |
| Existing account made optional | Keeps its current factor; only the user can turn it off, with their password. |
| Made required again | An account with no factor is switched to email codes immediately. |
| Self-service | An administrator cannot exempt their own account. |
| Traceability | The change is logged, and every sign-in records which factor was used. |

One problem surfaced: the running local database had no column for the new setting, because the
system creates tables at start-up and has no migrations. The agent backed up the database and added
the column by hand. This is now recorded as a concrete cost under "Known limits".

### Phase 11: export the activity log

Added a CSV download of the activity log to the API and the UI. It takes the same filters as the
search, is capped at 50,000 rows, protects against spreadsheet formula injection, and is itself
recorded in the log.

> "Both features work in the browser too (18 steps pass; the listed "problems" are the expected wrong-password and MFA-required responses). Updating the docs and logs to match."

After this round: 37 tests, 32 end-to-end checks and 18 browser steps, all passing.

### Phase 12: service review

The agent opened a review of the auth service with three decisions to keep or change (per-request
authorization calls, the password grant, account lockout). Not yet carried out when this copy was packaged.

---

## Result

| Deliverable | Where |
|---|---|
| Working system | `./scripts/run-local.sh start`, then http://localhost:8080 |
| Activity from planning to deployment | [ACTIVITY_LOG.md](ACTIVITY_LOG.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| API specification | [API.md](API.md), [API_ENDPOINTS.md](API_ENDPOINTS.md), [openapi/](openapi) |
| Deployment | [DEPLOYMENT.md](DEPLOYMENT.md) |
