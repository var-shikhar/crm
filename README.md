# CRM System

A basic CRM built as three microservices behind a gateway.

| Service | Responsibility |
|---|---|
| **auth-service** | Users, roles and permissions (RBAC), OAuth 2.0 tokens, MFA by email code or authenticator app. Decides every permission question for the other services. |
| **customer-service** | Customers: add, edit, block, unblock. Employment records and related contacts, each behind its own permission. |
| **audit-service** | Append-only activity log: who did what, to which record, when, from where. |
| **gateway** + **web** | Single entry point and a small browser UI. |

## Run it

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/), on Linux, macOS or WSL.
On Windows without WSL, see [Deployment](docs/DEPLOYMENT.md#on-windows-without-wsl).

```sh
./scripts/run-local.sh start     # prints the URL and the administrator password
```

- App: http://localhost:8080
- API docs (Swagger): http://localhost:8080/auth/docs, `/crm/docs`, `/audit/docs`

The administrator signs in with email and password. Every other user is then asked for a 6-digit
code. Locally the code is written to the log instead of emailed:

```sh
./scripts/run-local.sh code      # prints the latest code
```

Other commands: `stop`, `restart`, `status`, `logs`, `reset` (deletes local databases, keys and logs).

To run in containers: `./scripts/init-env.sh && docker compose up --build -d`
(see [Deployment](docs/DEPLOYMENT.md); configured and validated, not yet run).

## Test it

```sh
# 48 unit and API tests, one suite per service
for s in auth customer audit; do (cd services/$s-service && PYTHONPATH=../../shared ../../.venv/bin/python -m pytest -q); done

# 47 end-to-end checks against the running stack
.venv/bin/python scripts/smoke_test.py
```

## Who can do what

| Role | Can do |
|---|---|
| `admin` | Everything: customers, users, roles, everyone's activity, permanent deletion. Signs in without a code. |
| `manager` | All customer actions, including block and unblock, employment and contacts |
| `agent` | View, add and edit customers |
| `viewer` | View customers |

Every user can see the activity of their own account. Administrators can build further roles from
the [permission list](docs/services/auth-service.md#permissions-and-default-roles).

## Documentation

| Read this | To learn |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | How the parts fit: sign-in, authorization, logging, data model, known limits |
| [auth-service](docs/services/auth-service.md) · [customer-service](docs/services/customer-service.md) · [audit-service](docs/services/audit-service.md) | One service: what it does, endpoints, permissions, data, configuration |
| [API guide](docs/API.md) | Conventions, errors, `curl` examples |
| [Endpoint index](docs/API_ENDPOINTS.md) · [OpenAPI files](docs/openapi) | The full API specification, generated from the running services |
| [Deployment](docs/DEPLOYMENT.md) | Local run and container deployment |
| [Activity log](docs/ACTIVITY_LOG.md) | What was done, from planning to verification |
| [Prompt log](docs/PROMPTS.md) | The prompts and interaction with the coding agent |

## The brief, and where it is met

| Requirement | Where |
|---|---|
| Auth service with RBAC, OAuth and MFA, used by every other module | `services/auth-service` · [doc](docs/services/auth-service.md) |
| Customer management under access control, with employment and contacts | `services/customer-service` · [doc](docs/services/customer-service.md) |
| All activity logged with user and timestamp | `services/audit-service` · [doc](docs/services/audit-service.md) |
| Configured for containerized deployment | `docker-compose.yml`, `Dockerfile` per service, `gateway/` · [doc](docs/DEPLOYMENT.md) |
| Record of activity, planning to deployment | [Activity log](docs/ACTIVITY_LOG.md) |
| Record of prompts and interaction | [Prompt log](docs/PROMPTS.md) |
| Architecture, API specification, documentation per microservice | [Architecture](docs/ARCHITECTURE.md) · [OpenAPI](docs/openapi) · [docs/services](docs/services) |

## Layout

```
services/<name>/app      service code            services/<name>/tests   its tests
shared/crm_common        authz client, audit outbox, logging (copied into each image)
web/                     browser UI (static files, no build step)
gateway/                 nginx config for the container deployment
deploy/postgres/         database bootstrap for the container deployment
docker-compose.yml       container deployment
scripts/                 run-local.sh, smoke_test.py, export_openapi.py, init-env.sh, dev_gateway.py
docs/                    the documents listed above
```
