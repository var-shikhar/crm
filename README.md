# CRM System

A basic CRM built as three microservices behind a gateway:

| Service | Responsibility |
|---|---|
| **auth-service** | Users, roles, permissions (RBAC). OAuth 2.0 token service. MFA by email code or authenticator app (Google Authenticator), required by default and switchable per user by an administrator. Makes every authorization decision for the other services. |
| **customer-service** | Customers: add, edit, block/unblock. Employment records and related contacts, each behind its own permission. |
| **audit-service** | Append-only activity log: who did what, to which record, when, from where. Searchable and exportable as CSV. |
| **gateway** + **web** | Single entry point and a small browser UI. |

Documentation:

- [Architecture](docs/ARCHITECTURE.md): design, flows, data model, security decisions, known limits
- [API guide](docs/API.md) and [endpoint index](docs/API_ENDPOINTS.md); OpenAPI files in [docs/openapi](docs/openapi)
- [Deployment](docs/DEPLOYMENT.md): local run and container deployment
- [Activity log](docs/ACTIVITY_LOG.md): everything done from planning to verification
- [Prompt log](docs/PROMPTS.md): the prompts and interactions with the coding agent

## Run it locally (no Docker)

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
./scripts/run-local.sh start     # creates .venv and .local/, starts 4 processes
```

It prints the URL and the generated administrator password:

- App: http://localhost:8080
- API docs (Swagger): http://localhost:8080/auth/docs, `/crm/docs`, `/audit/docs`

Signing in asks for a 6-digit code. Locally there is no mail server, so the code is written to the
auth service log. Print the latest one with:

```sh
./scripts/run-local.sh code
```

Other commands: `stop`, `restart`, `status`, `logs`, `reset` (deletes local databases, keys and logs).

## Verify it

```sh
# Unit/API tests, one suite per service (41 tests)
for s in auth customer audit; do (cd services/$s-service && PYTHONPATH=../../shared ../../.venv/bin/python -m pytest -q); done

# End-to-end against the running stack (39 checks; needs a fresh database, where the admin still signs in by email code)
.venv/bin/python scripts/smoke_test.py
```

## Default roles

| Role | Can do |
|---|---|
| `admin` | Everything, including users, roles, the activity log, and permanently deleting customers and users |
| `manager` | All customer actions including block/unblock, employment and contacts; read the activity log; view users |
| `agent` | View, add and edit customers |
| `viewer` | View customers |

Administrators can create further roles from the permission catalogue (for example a role that may only
maintain employment records).

## Layout

```
services/<name>/app      service code          services/<name>/tests   its tests
shared/crm_common        authz client, audit outbox, logging (copied into each image)
gateway/                 nginx config for container deployment
web/                     browser UI (static files, no build step)
scripts/                 run-local.sh, dev_gateway.py, smoke_test.py, export_openapi.py, init-env.sh
deploy/postgres/         database bootstrap for the container stack
docker-compose.yml       container deployment
docs/                    architecture, API, deployment, activity and prompt logs
```
