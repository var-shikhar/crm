# audit-service

The activity log. It receives events from the other services, stores them, and lets users search
and export them: administrators everything, everyone else their own account's activity. Events can be added and read; nothing in the API changes or removes one.

| | |
|---|---|
| Code | `services/audit-service/app` (one module, `main.py`) |
| Tests | `services/audit-service/tests` (7 tests) |
| Gateway prefix | `/audit` |
| Local port | 8003 |
| Database | `audit_db` (PostgreSQL in containers, SQLite locally) |
| API specification | [openapi/audit-service.json](../openapi/audit-service.json); live at `/audit/docs` |

## Endpoints (under `/api/v1`)

| Endpoint | Purpose | Who may call |
|---|---|---|
| `GET /events` | Search the log, newest first, paged | Any signed-in user, within what they may see |
| `GET /events/export` | The same search as a CSV download, at most 50,000 rows | Any signed-in user, within what they may see |
| `GET /events/{event_id}` | One event; 404 if outside what the caller may see | Any signed-in user, within what they may see |
| `POST /events/batch` | Deliver up to 500 events | Services holding `audit:write`; not exposed by the gateway |

Search filters: `actor_id`, `actor_email` (contains), `action` (exact, or a prefix ending in `*`),
`service`, `resource_type`, `resource_id`, `outcome` (`success`, `failure`, `denied`), `from`, `to`.
Examples are in the [API guide](../API.md#activity-log).

## Who sees what

| Caller | Sees |
|---|---|
| Holds `audit:read` (the `admin` role only, by default) | Every event |
| Any other signed-in user | Events they caused, and events on their own user record: sign-in attempts on the account, and changes an administrator made to it |
| A service client | Nothing; reading needs a user |

The service asks the auth service to authenticate the token, then adds this rule to the database
query. Filters narrow the result and cannot widen it. Each read is logged with `scope: all` or `own`.

## What one event records

| Field | Content |
|---|---|
| `occurred_at`, `received_at` | When it happened and when the log received it (UTC) |
| `service`, `submitted_by` | Which service the action took place in, and which authenticated service delivered the event |
| `action`, `outcome` | For example `customer.blocked`, `success` |
| `actor_id`, `actor_type`, `actor_email`, `actor_name`, `actor_roles` | Who did it, with their roles at that moment |
| `resource_type`, `resource_id` | The record acted on |
| `ip_address`, `user_agent`, `request_id` | Where the request came from, and an id that ties it to the service logs |
| `details` | Specifics: changed fields with old and new values, a block reason, a refused permission |

## How events arrive

The auth and customer services write each event into their own `audit_outbox` table in the same
transaction as the change it describes. A background worker in each service delivers the events here
in batches, using a service token with `audit:write`. Delivery is repeated until it succeeds, and
this service ignores an `event_id` it already has, so an event is stored once. When this service is
down, events wait in the outboxes; when it is healthy the delay is up to about 2 seconds.

## Rules

- No update or delete endpoint exists.
- Users cannot write events: `audit:write` exists only on service clients and cannot be put in a role.
- Reading and exporting the log are themselves logged, with the filters used.
- In a CSV export, values a spreadsheet would run as a formula are neutralised. The response header
  `X-Truncated: true` marks an export cut off at the row limit.

## Data

One table, `audit_events`, keyed by `event_id`.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./audit.db` | Database |
| `AUTH_URL` | `http://localhost:8001` | auth-service base URL (internal address) |
| `CLIENT_ID`, `CLIENT_SECRET` | `audit-service`, empty | This service's OAuth client, used to check callers' tokens |
| `LOG_LEVEL` | `INFO` | |

## Run and test

```sh
cd services/audit-service
PYTHONPATH=../../shared python -m pytest -q
PYTHONPATH=../../shared python -m uvicorn app.main:app --port 8003 --root-path /audit
```

Health: `/healthz`, `/readyz`.

## Known limits

The log has no tamper-evidence, such as a hash chain, beyond having no write API for users. There is
no retention or archiving job.
