# customer-service

Holds customer records and the two kinds of additional record attached to a customer: employment
and related contacts. It contains no sign-in or role logic. Every request is checked with the
auth service, and every action is written to the activity log.

| | |
|---|---|
| Code | `services/customer-service/app` |
| Tests | `services/customer-service/tests` (11 tests) |
| Gateway prefix | `/crm` |
| Local port | 8002 |
| Database | `customer_db` (PostgreSQL in containers, SQLite locally) |
| API specification | [openapi/customer-service.json](../openapi/customer-service.json); live at `/crm/docs` |

## What it does, and the permission each action needs

| Action | Endpoint (under `/api/v1`) | Permission |
|---|---|---|
| Search and list customers | `GET /customers` | `customer:read` |
| View one customer | `GET /customers/{id}` | `customer:read` |
| Add a customer | `POST /customers` | `customer:create` |
| Edit a customer | `PATCH /customers/{id}` | `customer:update` |
| Block (a reason is required) | `POST /customers/{id}/block` | `customer:block` |
| Unblock | `POST /customers/{id}/unblock` | `customer:block` |
| Delete permanently | `DELETE /customers/{id}` | `customer:delete` |
| List employment records | `GET /customers/{id}/employment` | `customer:read` |
| Add, edit, remove employment | `POST`, `PATCH`, `DELETE /customers/{id}/employment…` | `customer:employment:manage` |
| List related contacts | `GET /customers/{id}/contacts` | `customer:read` |
| Add, edit, remove contacts | `POST`, `PATCH`, `DELETE /customers/{id}/contacts…` | `customer:contact:manage` |

Employment and contacts each have their own permission, so a role can be allowed to maintain those
records without being allowed to edit, block or delete the customer.

## Rules

- A customer's email is unique, ignoring case.
- Blocking records the reason, who did it and when.
- A blocked customer is read-only, together with its employment and contacts. Edits and deletion
  return `409` until it is unblocked.
- Deleting removes the customer with its employment and contacts. The activity log keeps a copy.
- Names and places accept letters of any script, phone numbers have 7 to 15 digits, a date of birth
  lies between 1900 and today, and a current job has no end date. The rules live in
  `shared/crm_common/validation.py`.

## How a request is authorized

For each request the service sends the caller's token and the permission it needs to
`auth-service /api/v1/authz/check`, authenticating as the OAuth client `customer-service`. The
answer names the user and says allowed or not. If the auth service cannot be reached the request is
refused with `503`. Sequence diagram: [ARCHITECTURE.md](../ARCHITECTURE.md#3-authorization-central-rbac).

## Data

| Table | Main fields |
|---|---|
| `customers` | name, email, phone, date of birth, address, notes, `status` (`active` or `blocked`), block reason, blocked by and at, created and updated by and at |
| `customer_employments` | employer, job title, employment type, start and end date, current or not, annual income, currency |
| `customer_contacts` | full name, relationship, email, phone, emergency contact or not, notes |
| `audit_outbox` | activity events waiting to be delivered |

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./customer.db` | Database |
| `AUTH_URL` | `http://localhost:8001` | auth-service base URL (internal address) |
| `CLIENT_ID`, `CLIENT_SECRET` | `customer-service`, empty | This service's OAuth client; the secret must match the one the auth service holds |
| `AUDIT_URL` | empty | audit-service base URL; empty keeps events in the outbox |
| `LOG_LEVEL` | `INFO` | |

## Events it writes to the activity log

`customer.searched`, `customer.viewed`, `customer.created`, `customer.updated` (each changed field
with its old and new value), `customer.blocked`, `customer.unblocked`, `customer.deleted`, and for
employment and contacts `customer.employment.*` and `customer.contact.*` (`viewed`, `added`,
`updated`, `removed`). An event is written in the same database transaction as the change, then
delivered to the audit service in the background.

## Run and test

```sh
cd services/customer-service
PYTHONPATH=../../shared python -m pytest -q
PYTHONPATH=../../shared python -m uvicorn app.main:app --port 8002 --root-path /crm
```

The tests replace the auth service with a stub, so they run on their own. Health: `/healthz`, `/readyz`.
