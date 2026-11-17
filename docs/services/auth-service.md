# auth-service

The one place that knows who a user is and what they may do. It is an OAuth 2.0 token service, the
user and role store (RBAC), and the multi-factor sign-in. The other services hold no authentication
or role logic; they ask this service about every request.

| | |
|---|---|
| Code | `services/auth-service/app` |
| Tests | `services/auth-service/tests` (30 tests) |
| Gateway prefix | `/auth` |
| Local port | 8001 |
| Database | `auth_db` (PostgreSQL in containers, SQLite locally) |
| API specification | [openapi/auth-service.json](../openapi/auth-service.json); live at `/auth/docs` |

## What it does

| Area | Behaviour |
|---|---|
| Sign-in | Email and password (OAuth password grant), then a one-time code (a custom MFA grant) |
| Second factor | Emailed 6-digit code by default; authenticator app (Google Authenticator, TOTP) once the user enrols |
| Tokens | RS256 JWT access token (15 min), opaque refresh token (7 days) rotated on each use; public keys at `/.well-known/jwks.json` |
| Users | Create, edit, deactivate, unlock, reset password, reset second factor, delete |
| Roles | Named sets of permissions; four are seeded, administrators can add more |
| Decisions | `POST /api/v1/authz/check`: validates a caller's token and answers one permission question, or only authenticates it when no permission is sent |
| Service clients | `customer-service` and `audit-service` authenticate with client id and secret |

## Endpoints

Full list with required permission: [API_ENDPOINTS.md](../API_ENDPOINTS.md). In groups:

| Group | Paths | Who may call |
|---|---|---|
| OAuth | `/oauth/token`, `/oauth/revoke`, `/.well-known/*` | Anyone with a registered client id |
| My account | `/api/v1/me`, `/me/password`, `/me/mfa/*` | Any signed-in user |
| Users | `/api/v1/users…` | `user:read` to view, `user:manage` to change |
| Roles | `/api/v1/roles…`, `/api/v1/permissions` | `role:read` to view, `role:manage` to change |
| Internal | `/api/v1/authz/check`, `/oauth/introspect` | Service clients only; not exposed by the gateway |

## Permissions and default roles

The catalogue is fixed in `app/seed.py`. Roles are data.

| Permission | Meaning | admin | manager | agent | viewer |
|---|---|:-:|:-:|:-:|:-:|
| `user:read` | View users | ✓ | | | |
| `user:manage` | Create and update users, assign roles, reset MFA | ✓ | | | |
| `role:read` | View roles and permissions | ✓ | | | |
| `role:manage` | Create, update and delete roles | ✓ | | | |
| `customer:read` | View customers and their related records | ✓ | ✓ | ✓ | ✓ |
| `customer:create` | Add customers | ✓ | ✓ | ✓ | |
| `customer:update` | Edit customers | ✓ | ✓ | ✓ | |
| `customer:block` | Block and unblock customers | ✓ | ✓ | | |
| `customer:delete` | Permanently delete customers | ✓ | | | |
| `customer:employment:manage` | Add, edit and remove employment records | ✓ | ✓ | | |
| `customer:contact:manage` | Add, edit and remove related contacts | ✓ | ✓ | | |
| `audit:read` | View and export everyone's activity; without it a user sees only their own | ✓ | | | |
| `auth:mfa:skip` | Sign in with email and password only, with no code | ✓ | | | |

`admin` is a system role: it always holds every permission and cannot be edited or deleted. The
list of users and other people's activity are therefore visible to administrators only, unless an
administrator puts `user:read` or `audit:read` into another role. The other three roles are created
once; later changes to their defaults in code do not alter a database that already has them.

## When a sign-in asks for a code

| Account | Result of the password step |
|---|---|
| Default (second factor required, no `auth:mfa:skip`) | `403 mfa_required`; a code completes the sign-in |
| A role grants `auth:mfa:skip` | Tokens at once. This wins over any second factor the account has set up |
| Administrator made MFA optional for the user, and the user has no second factor | Tokens at once |

Because `admin` holds every permission, administrators sign in with email and password only. To let
other people do the same, put `auth:mfa:skip` in a role and give them that role. To see the code step,
sign in as any user without that permission. Each sign-in is logged with the factor used; a sign-in
that skipped the code by permission carries `mfa_skipped_by: auth:mfa:skip`.

## Data

`users`, `roles`, `permissions`, `user_roles`, `role_permissions`, `oauth_clients`, `refresh_tokens`
(hashed), `mfa_challenges` (code stored as a hash), `revoked_tokens`, `audit_outbox`. Diagram in
[ARCHITECTURE.md](../ARCHITECTURE.md#6-data-model). Tables are created at start-up; there are no
migrations yet.

## Configuration (environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./auth.db` | Database |
| `ISSUER` | `http://localhost:8080/auth` | Public URL of this service; written into tokens |
| `JWT_PRIVATE_KEY_PATH` | `./keys/jwt_private.pem` | Signing key; generated on first start |
| `SECRET_KEY` | development value | Encrypts authenticator secrets at rest. Must be set |
| `ACCESS_TOKEN_TTL`, `REFRESH_TOKEN_TTL` | 900 s, 7 days | Token lifetimes |
| `MAX_FAILED_LOGINS`, `LOCKOUT_MINUTES` | 5, 15 | Lockout after repeated wrong passwords |
| `MFA_CHALLENGE_TTL`, `MFA_MAX_ATTEMPTS` | 300 s, 5 | Life of a code and wrong tries allowed |
| `EMAIL_BACKEND` | `console` | `smtp`, `console` (code written to the log) or `memory` (tests) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `EMAIL_FROM` | Mailpit-style | Mail relay for codes |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, `BOOTSTRAP_ADMIN_NAME` | | First administrator, created only while no administrator exists |
| `CUSTOMER_SERVICE_CLIENT_SECRET`, `AUDIT_SERVICE_CLIENT_SECRET` | | Secrets of the two service clients |
| `AUDIT_URL` | empty | audit-service base URL; empty keeps events in the outbox |

## Events it writes to the activity log

Sign-in (`auth.mfa.challenged`, `auth.mfa.failed`, `auth.login.succeeded`, `auth.login.failed`,
`auth.account.locked`, `auth.logout`, `auth.refresh.reuse_detected`), account (`user.password.changed`,
`user.password.reset`, `user.mfa.*`, `user.unlocked`), administration (`user.created/updated/deleted`,
`role.created/updated/deleted`) and every refused permission check (`authz.denied`).

## Run and test

```sh
cd services/auth-service
PYTHONPATH=../../shared python -m pytest -q
PYTHONPATH=../../shared python -m uvicorn app.main:app --port 8001 --root-path /auth
```

Health: `/healthz` (process up), `/readyz` (database reachable).
