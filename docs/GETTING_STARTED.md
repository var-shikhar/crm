# Getting started

How to start the CRM, sign in, and check that it works. Pick one of the two ways to run it.

| | Option A: local processes | Option B: containers |
|---|---|---|
| Needs | Python 3.13, `uv`, `curl` | Docker with Compose |
| Database | SQLite files in `.local/` | PostgreSQL |
| Sign-in codes appear in | the auth service log | the Mailpit inbox |
| Status | Run and tested | Configured and validated, not yet run |

## Option A: local processes (Linux, macOS, WSL)

**1. Install the tools** (once):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs uv; it fetches Python 3.13 if missing
```

**2. Start:**

```sh
git clone https://github.com/var-shikhar/crm.git && cd crm
./scripts/run-local.sh start
```

The first start creates `.venv/` and `.local/`, then prints:

```
CRM is running:  http://localhost:8080
  Sign in as:    admin@example.com / <generated password>
```

**3. Open** http://localhost:8080 and sign in with that email and password.

The services listen on `127.0.0.1` only. To reach them from another machine, forward the port:
`ssh -L 8080:localhost:8080 <user>@<server>`, then open http://localhost:8080 on your own machine.

**Day to day:**

| Command | Does |
|---|---|
| `./scripts/run-local.sh status` | Shows which of the four processes are running |
| `./scripts/run-local.sh code` | Prints the latest sign-in code |
| `./scripts/run-local.sh logs` | Follows the logs |
| `./scripts/run-local.sh stop` / `restart` | Stops or restarts everything |
| `./scripts/run-local.sh reset` | Stops and deletes `.local/` (databases, keys, logs, admin password) |

The admin password is printed at every start and kept in `.local/env`.

## Option B: containers

```sh
git clone https://github.com/var-shikhar/crm.git && cd crm
./scripts/init-env.sh              # writes .env with random secrets and prints the admin password
docker compose up --build -d
docker compose ps                  # wait until the services are healthy
```

Open http://localhost:8080 (or `http://<server>:8080`). Sign-in codes arrive in the Mailpit inbox at
http://localhost:8025, which is reachable from the server itself only.

Stop with `docker compose down`. Add `-v` to delete the databases too. Details and production notes
are in [DEPLOYMENT.md](DEPLOYMENT.md).

## First steps in the app

1. **Sign in as the administrator.** Email and password only; administrators are not asked for a code.
2. **Create a user.** Users → fill in the form, choose a role (`manager`, `agent` or `viewer`).
3. **Sign in as that user** in a private window. After the password the app asks for a 6-digit code.
   Get it with `./scripts/run-local.sh code` (Option A) or from Mailpit (Option B).
4. **Work with customers.** Add one, edit it, and as a manager block and unblock it and add
   employment records and contacts. An agent sees no block button; a viewer can only read.
5. **Look at the activity.** The user sees their own actions under Activity. The administrator sees
   everyone's, and can download it as CSV.
6. **Optional: authenticator app.** My security → Set up an authenticator app, and scan the QR code
   with Google Authenticator.

What each role may do is listed in [services/auth-service.md](services/auth-service.md#permissions-and-default-roles).

## Check that it works

With Option A running:

```sh
# 48 unit and API tests
for s in auth customer audit; do (cd services/$s-service && PYTHONPATH=../../shared ../../.venv/bin/python -m pytest -q); done

# 47 end-to-end checks against the running system (creates a few test users and customers)
.venv/bin/python scripts/smoke_test.py
```

The API can be tried in the browser at http://localhost:8080/auth/docs, `/crm/docs` and `/audit/docs`.
Examples with `curl` are in [API.md](API.md).

## If something goes wrong

| Symptom | Fix |
|---|---|
| `uv: not found` | Open a new shell after installing uv, or add `~/.local/bin` to `PATH` |
| `timed out waiting for http://127.0.0.1:8001/readyz` | A port from 8001 to 8003 or 8080 is in use, or a service failed. Read `.local/logs/auth.log` |
| "already running" but the page does not load | `./scripts/run-local.sh restart` |
| No code arrives | Option A: `./scripts/run-local.sh code`. Option B: open Mailpit on port 8025 |
| Account locked after wrong passwords | Wait 15 minutes, or have an administrator unlock it under Users |
| Forgot the admin password | Option A: it is in `.local/env`. Option B: it is in `.env` |
| Windows without WSL | See [DEPLOYMENT.md](DEPLOYMENT.md#on-windows-without-wsl) |
