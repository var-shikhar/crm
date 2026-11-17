# Deployment

## 1. Local, without containers (the mode that was run and verified)

`./scripts/run-local.sh start` runs four processes from one virtualenv:

| Process | Port | Storage |
|---|---|---|
| auth-service | 8001 | `.local/auth.db` (SQLite), signing key in `.local/keys/` |
| customer-service | 8002 | `.local/customer.db` |
| audit-service | 8003 | `.local/audit.db` |
| dev gateway (`scripts/dev_gateway.py`) | 8080 | serves `web/`, proxies `/auth`, `/crm`, `/audit` |

Secrets (service client secrets, encryption key, admin password) are generated once into `.local/env`
(mode 600). Email codes use the `console` backend and appear in `.local/logs/auth.log`.
`scripts/dev_gateway.py` mirrors the nginx rules (prefix routing, internal endpoints hidden, security
headers) but has no rate limiting. It is a development tool, not a production gateway.

### On Windows without WSL

`run-local.sh` expects a POSIX layout (`.venv/bin/python`) and `uv`, so it does not run as it is in
Git Bash. The same four processes can be started by hand from a virtualenv made with
`python -m venv .venv`, using the commands and environment variables in the `start` function of the
script (with `.venv/Scripts/python.exe`, and Windows paths in `DATABASE_URL`). The system was run and
checked this way on Windows 11. If Windows application control blocks SQLAlchemy's compiled
extensions, install it as pure Python: `pip install --no-binary sqlalchemy sqlalchemy`.

`scripts/smoke_test.py` reads the admin password and the emailed codes from `.local/`. Set
`CRM_LOCAL_DIR` and `BASE_URL` to point it at a stack that keeps them elsewhere.

## 2. Containers (configured; not run)

Status, stated plainly: the container setup is written and `docker compose config` validates it. The three
service images were built successfully once, before the start command in the Dockerfiles was last
changed. **The stack has never been started in containers**, so treat it as untested until you run
`docker compose up` and the smoke test against it.

```sh
./scripts/init-env.sh            # writes .env with random secrets, prints the admin password
docker compose up --build -d
```

| Container | Image | Notes |
|---|---|---|
| `gateway` | nginx (unprivileged) + `web/` | Only published port (8080). Rate-limits the token endpoint. Hides internal endpoints. |
| `auth-service`, `customer-service`, `audit-service` | `python:3.13-slim` | Non-root user, read-only root filesystem, all capabilities dropped, health checks on `/readyz`. |
| `postgres` | `postgres:16-alpine` | One database and one login role per service (`deploy/postgres/init-databases.sh`). |
| `mailpit` | `axllent/mailpit` | Development inbox for email codes at http://localhost:8025. |

Services are configured only through environment variables (see `.env.example`), so the same images
run under any orchestrator. For Kubernetes, each service maps to a Deployment + Service, the gateway
to an Ingress, and the variables in `.env` to a Secret; `/healthz` is the liveness probe and `/readyz`
the readiness probe.

### Before production

- Set `SMTP_*` to a real mail relay and remove `mailpit`.
- Terminate TLS in front of the gateway and set `ISSUER` to the public `https://…/auth` URL.
- Provide secrets from a secret manager, not a `.env` file.
- Replace `create_all` at startup with versioned migrations (Alembic) before the first schema change.
- Run one auth-service replica, or move the startup seed into a migration job, before scaling it out.
