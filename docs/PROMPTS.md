# Prompt and interaction log

The CRM in this repository was built with a coding agent (Claude Code, model Claude Fable 5.2): the
first build in one working session on 14 November 2026, then follow-up rounds on 16 November 2026.
This file records the prompts that drove the work and what the agent did in response, phase by
phase. User prompts are quoted as written; the agent's side is summarised, with its own progress
messages quoted where they mark a step.

The complete transcript of the first session, with every tool call and its output, is kept by
Claude Code in its local session history (session `649434f8-2ace-4d94-a8fa-09d363404404`). The later
rounds ran in separate sessions.

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
authorization calls, the password grant, account lockout). The review was not taken further in the recorded
sessions; all three stand as described in ARCHITECTURE.md.

---

## Round 3: fixes after trying the system (16 November 2026)

The wording of the prompts in this round was not kept, so they are summarised, not quoted. After
using the running system the user asked for: deleting a customer, deleting a user, stricter checks
on form input, and a look at "change password", which had appeared to fail.

### Phase 13: deletion, validation, password check

Added `DELETE /customers/{id}` behind a new permission held by `admin` only, `DELETE /users/{id}`
(refused for your own account and for the last active administrator), and one shared set of input
rules used by the services and repeated in the UI. The password problem could not be reproduced and
no code was changed for it. After this round: 41 tests, 39 end-to-end checks.

### Phase 14: repository and UI polish

The work was committed to git for the first time. Paths of the local machine were removed from the
documents, and the web UI was adjusted for phones and for accessibility (details in ACTIVITY_LOG.md,
section 10). The prompts for these steps were not kept either.

---

## Prompt 4: where the project stands, and two tasks

> So how far are we on this project ?

Followed, while the agent was reading the repository, by:

> Have few tasks planned as:
>
> * Admin users can be logged-in without the MFA or Any code (or rather we should be having a permission which allows any account to get logged-in without the code: much better)
> * Documentation:
>   * Is the documentation clear cut and says exactly about the project
>   * Is it divided based on its role
>   * Is it satisfies assignments task (assignment given below)'

The message ended with the brief from Prompt 1, repeated in full.

### Phase 15: sign-in without a code, by permission

The agent took the second form of the request, a permission, and added `auth:mfa:skip`. Decisions
taken while building it:

| Question | Decision |
|---|---|
| Who holds it | Any role an administrator puts it in. `admin` holds every permission, so administrators get it without further action, which is the first form of the request. |
| Account with an authenticator app or email codes set up | The permission wins; no code is asked. The account's own setting is left alone, so the code returns when the permission goes. |
| Per-user "MFA optional" from round 2 | Kept as it is. The two are independent. |
| Traceability | The sign-in event records that the code was skipped by permission, and the user list shows it. |
| Risk | Stated in ARCHITECTURE.md: an administrator's password alone now opens the most powerful accounts. |

Five existing tests that used the administrator to exercise the code step were moved to an ordinary
user, and four were added.

> "Sign-in permission is coded and tests are written; I'm now getting the test suite to run on this machine (Windows is blocking one of the freshly installed Python libraries)."

> "All 45 unit tests pass with the new permission. Now updating the web UI and the end-to-end script, then the documentation review."

> "Feature verified end to end (41 checks pass on a throwaway copy of the stack). Fixing a path glitch in the regenerated API spec, then auditing the docs against the assignment."

### Phase 16: documentation review

The agent read every document against the three questions and the brief. Answers:

| Question | Answer |
|---|---|
| Clear, and says exactly what the project is? | Mostly. The content was accurate and plain about limits, but several statements had gone stale (test counts, "nothing was committed", a prompt log that stopped at round 2). Corrected. |
| Divided by role? | By topic, yes; by microservice, no. Added one page per service and a table in the README saying what each document is for. |
| Satisfies the brief? | Each of the four features and three records is present. Two gaps were closed (per-service documentation, a map from the brief to the repository). One remains and is stated wherever it matters: the container deployment is configured and validated but has never been run. |

---

## Prompt 5: who sees users and activity

> Got few points as well:
>
> * I think the users should only be visible only to admins
> * Activity Logs should be user based (account: same account activity to user, unless its an admin account)

### Phase 17: visibility

| Question | Decision |
|---|---|
| What makes an account "admin" here | Holding the permission (`user:read`, `audit:read`), which by default only the `admin` role does. The `manager` role lost both. |
| Who may open the activity log | Every signed-in user. Without `audit:read` the audit service limits the query to the caller's own account. |
| What counts as the account's activity | What the user did, and what was done to their user record (sign-in attempts, changes by an administrator). |
| How the audit service learns who is calling | The auth service's decision endpoint can now be asked to authenticate a token without a permission. |
| The database that already existed | Default roles are seeded once, so its `manager` role was edited through the API. |

> "Both changes are coded (users list admin-only, activity log scoped to your own account unless you hold `audit:read`); fixing an import and running the tests now."

> "End-to-end passes (47 checks). Now fixing the generated endpoint index label, updating the docs, and applying the change to your running stack."

After this round: 48 tests, 47 end-to-end checks.

---

## Prompt 6: tighten the documents and push

> Few Updates:
>
> - Make sure your documentation is crisp and easy to follow
> - Push the current changes (previous task if done)

### Phase 18: documentation pass and push

The README was reordered and shortened, the longest passages of ARCHITECTURE.md were turned into
tables, and the work of prompts 4 and 5 was committed and pushed.

---

## Prompt 7: dead code, a summary, and a start guide

> So you tell me few things as:
>
> - Have we checked the code and has no dead-code
> - The documentation are crisp as per the requirements
>
> Now I need a summary message as per the task (so I can revert the same)

and then:

> Cool, One update I need both in doc and for email as: I needed a way (how to start/run the project) kinda thing which is clearly mentioned in readme or anywhere which is referenced in readmd (all md should be referenced in readme okay)

### Phase 19: dead-code check and getting-started guide

The code had not been checked for dead code before, so the agent ran pyflakes and vulture over the
Python code and a script over the web UI. One unused constant was removed. It then wrote
`GETTING_STARTED.md`, linked it from the README, confirmed that every Markdown file is linked from
the README, and drafted the summary message for the user to send.

---

## Result

| Deliverable | Where |
|---|---|
| Working system | [GETTING_STARTED.md](GETTING_STARTED.md): `./scripts/run-local.sh start`, then http://localhost:8080 |
| Activity from planning to deployment | [ACTIVITY_LOG.md](ACTIVITY_LOG.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Documentation per microservice | [services/](services) |
| API specification | [API.md](API.md), [API_ENDPOINTS.md](API_ENDPOINTS.md), [openapi/](openapi) |
| Deployment | [DEPLOYMENT.md](DEPLOYMENT.md) |
