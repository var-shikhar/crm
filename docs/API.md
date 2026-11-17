# API guide

- Every endpoint, with its required permission: [API_ENDPOINTS.md](API_ENDPOINTS.md) (generated)
- One page per service: [services/](services)
- Complete schemas: [openapi/auth-service.json](openapi/auth-service.json),
  [openapi/customer-service.json](openapi/customer-service.json),
  [openapi/audit-service.json](openapi/audit-service.json)
- Interactive (when running): `http://localhost:8080/auth/docs`, `/crm/docs`, `/audit/docs`

Regenerate the first two after changing an endpoint: `python scripts/export_openapi.py`.

## Conventions

| Topic | Rule |
|---|---|
| Base URLs (via gateway) | `/auth` auth-service, `/crm` customer-service, `/audit` audit-service |
| Authentication | `Authorization: Bearer <access_token>` on everything except the OAuth and `.well-known` endpoints |
| Bodies | JSON, except `/oauth/*` which are form-encoded as OAuth requires |
| IDs and times | UUIDs; ISO 8601 timestamps in UTC |
| Partial update | `PATCH` changes only the fields you send; send `null` to clear an optional field |
| Paging | `page` (from 1) and `page_size`; responses carry `items`, `total`, `page`, `page_size` |
| Tracing | Every response has `X-Request-ID`; the same id is stored on audit events |

Errors:

| Status | Meaning | Body |
|---|---|---|
| 400 | Rule violated (e.g. changing your own roles) | `{"detail": "..."}` |
| 401 | Missing, expired or revoked token; disabled account | `{"detail": "Invalid or expired token"}` |
| 403 | Signed in, but lacking the permission | `{"detail": "Missing permission: customer:block"}` |
| 404 | No such record | `{"detail": "Customer not found"}` |
| 409 | Conflict: duplicate email, customer blocked, role in use | `{"detail": "..."}` |
| 422 | Invalid input | `{"detail": [{"loc": [...], "msg": "..."}]}` |
| 429 | Too many token requests (gateway) | |
| 503 | Auth service unreachable; nothing is allowed through | `{"detail": "Authorization service unavailable"}` |

OAuth endpoints use the OAuth error shape instead: `{"error": "invalid_grant", "error_description": "..."}`.

## Sign in (two steps)

```sh
BASE=http://localhost:8080

# Step 1: password. Answers 403 mfa_required. (An account whose role grants auth:mfa:skip, which
# includes every administrator, or an account exempted from MFA with no second factor, gets the
# token response here directly and skips step 2.)
curl -s $BASE/auth/oauth/token \
  -d grant_type=password -d client_id=crm-web \
  -d username=ravi@example.com -d password='<password>'
# {"error":"mfa_required","mfa_token":"…","mfa_method":"email","expires_in":300, ...}

# Step 2: the code from email (locally: ./scripts/run-local.sh code) or the authenticator app.
curl -s $BASE/auth/oauth/token \
  -d grant_type=urn:crm:params:oauth:grant-type:mfa-otp -d client_id=crm-web \
  -d mfa_token='<mfa_token>' -d otp=123456
# {"access_token":"…","token_type":"Bearer","expires_in":900,"refresh_token":"…","scope":"customer:read …"}
```

Renew and sign out:

```sh
curl -s $BASE/auth/oauth/token -d grant_type=refresh_token -d client_id=crm-web -d refresh_token='<refresh_token>'
curl -s $BASE/auth/oauth/revoke -d client_id=crm-web -d token='<refresh_token>'
```

Each refresh returns a new refresh token; the old one stops working. Using an old one again ends the session.

## Make MFA optional for one user

```sh
# Administrator: at creation, or later
curl -s -X PATCH $BASE/auth/api/v1/users/<user_id> -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"mfa_required": false}'

# The user may then turn their second factor off (403 while it is required)
curl -s -X POST $BASE/auth/api/v1/me/mfa/disable -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"password":"<password>"}'
```

## Let a role sign in without a code

```sh
# Any role may carry the permission auth:mfa:skip. Everyone holding the role then signs in with
# email and password only. The admin role always has it.
curl -s -X POST $BASE/auth/api/v1/roles -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"trusted_desk","description":"No sign-in code","permissions":["auth:mfa:skip"]}'
curl -s -X PATCH $BASE/auth/api/v1/users/<user_id> -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"roles":["agent","trusted_desk"]}'
# The user's record now shows "mfa_skipped": true
```

## Enrol an authenticator app

```sh
curl -s -X POST $BASE/auth/api/v1/me/mfa/totp/setup -H "Authorization: Bearer $TOKEN"
# {"secret":"BASE32…","otpauth_uri":"otpauth://totp/CRM:…","qr_svg":"<svg…"}
curl -s -X POST $BASE/auth/api/v1/me/mfa/totp/activate -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"otp":"123456"}'
```

## Users and roles

```sh
# Create a role that may only maintain employment records and contacts
curl -s -X POST $BASE/auth/api/v1/roles -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"records_officer","description":"Employment and contacts",
       "permissions":["customer:read","customer:employment:manage","customer:contact:manage"]}'

# Create a user with that role
curl -s -X POST $BASE/auth/api/v1/users -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"ravi@example.com","full_name":"Ravi Kumar","password":"at-least-10-chars","roles":["records_officer"]}'

# Change roles or deactivate (applies to the user's next request)
curl -s -X PATCH $BASE/auth/api/v1/users/<user_id> -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"roles":["manager"],"is_active":true}'
```

## Customers

```sh
curl -s -X POST $BASE/crm/api/v1/customers -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"first_name":"Priya","last_name":"Sharma","email":"priya@example.com","phone":"+91 98765 43210"}'

curl -s "$BASE/crm/api/v1/customers?q=sharma&status=active&page=1" -H "Authorization: Bearer $TOKEN"

curl -s -X PATCH $BASE/crm/api/v1/customers/<id> -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"city":"Bengaluru"}'

curl -s -X POST $BASE/crm/api/v1/customers/<id>/block -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"reason":"KYC documents expired"}'
curl -s -X POST $BASE/crm/api/v1/customers/<id>/unblock -H "Authorization: Bearer $TOKEN"

curl -s -X POST $BASE/crm/api/v1/customers/<id>/employment -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"employer_name":"Infosys","job_title":"Analyst","start_date":"2021-04-01","annual_income":"1800000.00","currency":"INR"}'

curl -s -X POST $BASE/crm/api/v1/customers/<id>/contacts -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"full_name":"Rahul Sharma","relationship_type":"spouse","is_emergency_contact":true}'
```

## Activity log

A caller with `audit:read` (administrators) searches everyone's activity. Any other signed-in user
gets the same endpoints limited to their own account, whatever filters they send.

```sh
# Everything done to one customer
curl -s "$BASE/audit/api/v1/events?resource_id=<customer_id>" -H "Authorization: Bearer $TOKEN"
# Denied attempts by one person, failed logins, a time window
curl -s "$BASE/audit/api/v1/events?action=authz.denied&actor_email=ravi" -H "Authorization: Bearer $TOKEN"
curl -s "$BASE/audit/api/v1/events?action=auth.login.*&outcome=failure&from=2026-11-14T00:00:00Z" -H "Authorization: Bearer $TOKEN"
```

Download as CSV (same filters, newest first, at most 50,000 rows; response header `X-Truncated: true`
if more matched):

```sh
curl -s -o activity.csv "$BASE/audit/api/v1/events/export?action=customer.*&from=2026-11-01T00:00:00Z" \
  -H "Authorization: Bearer $TOKEN"
```

Filters: `actor_id`, `actor_email` (contains), `action` (exact, or prefix with `*`), `service`,
`resource_type`, `resource_id`, `outcome` (`success` | `failure` | `denied`), `from`, `to`.

Shape of one event (illustrative values):

```json
{
  "event_id": "5d0c…", "occurred_at": "2026-11-14T17:16:02.412Z", "received_at": "2026-11-14T17:16:03.9Z",
  "service": "customer-service", "submitted_by": "customer-service",
  "action": "customer.blocked", "outcome": "success",
  "actor_id": "8a41…", "actor_type": "user", "actor_email": "manager@example.com",
  "actor_name": "Meera Manager", "actor_roles": ["manager"],
  "resource_type": "customer", "resource_id": "c1f2…",
  "ip_address": "203.0.113.7", "user_agent": "Mozilla/5.0 …", "request_id": "b7e1…",
  "details": {"reason": "KYC documents expired"}
}
```

## Service-to-service (not reachable through the gateway)

| Endpoint | Caller authenticates with | Purpose |
|---|---|---|
| `POST auth-service /api/v1/authz/check` | HTTP Basic, client with `authz:check` | Validate a token and decide one permission |
| `POST auth-service /oauth/introspect` | HTTP Basic, client with `authz:check` | RFC 7662 token introspection |
| `POST auth-service /oauth/token` (`client_credentials`) | HTTP Basic | Service token for calling other services |
| `POST audit-service /api/v1/events/batch` | Bearer service token with `audit:write` | Deliver up to 500 events; idempotent on `event_id` |

To add a new microservice: register an OAuth client for it in `services/auth-service/app/seed.py`,
add its permissions to the catalogue there, then in the service use
`AuthzClient(...).requires("your:permission")` as the FastAPI dependency and `AuditEmitter.emit(...)`
for its events (both in `shared/crm_common`).
