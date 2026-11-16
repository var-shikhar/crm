# System architecture

## 1. Overview

```mermaid
flowchart LR
    U[Browser / API client]
    subgraph Edge
        G[Gateway<br/>nginx in containers,<br/>dev_gateway.py locally]
        W[Web UI<br/>static files]
    end
    subgraph Services
        A[auth-service<br/>OAuth 2.0, MFA, RBAC]
        C[customer-service<br/>customers, employment, contacts]
        L[audit-service<br/>activity log]
    end
    DA[(auth_db)]
    DC[(customer_db)]
    DL[(audit_db)]
    M[Email / SMTP]

    U --> G
    G --> W
    G -- /auth --> A
    G -- /crm --> C
    G -- /audit --> L
    C -- "authz/check (every request)" --> A
    L -- "authz/check (every request)" --> A
    C -- "events (outbox)" --> L
    A -- "events (outbox)" --> L
    A --> DA
    C --> DC
    L --> DL
    A -- one-time codes --> M
```

Each service owns its database; no service reads another's tables. The only shared code is
`shared/crm_common` (authorization client, audit outbox, logging), which is copied into each image
at build time so services still build and deploy independently.

| Component | Stack |
|---|---|
| Services | Python 3.13, FastAPI, SQLAlchemy 2 |
| Storage | PostgreSQL in containers (one database and login per service); SQLite for local runs and tests |
| Tokens | RS256 JWT access tokens (PyJWT), opaque refresh tokens |
| Passwords / MFA | Argon2id; TOTP (RFC 6238) and emailed one-time codes |
| Gateway | nginx; a small Starlette proxy stands in for it locally |
| UI | Plain HTML/CSS/JS, no build step |

## 2. Authentication (OAuth 2.0 + MFA)

The auth service is an OAuth 2.0 authorization server. Supported grants:

| Grant | Used by | Notes |
|---|---|---|
| `password` | Web UI (public client `crm-web`) | Step 1 of sign-in. Answers `403 mfa_required` with an `mfa_token`. Returns tokens directly only for an account an administrator has exempted from MFA and that has no second factor. |
| `urn:crm:params:oauth:grant-type:mfa-otp` | Web UI | Step 2: `mfa_token` + one-time code returns the tokens. |
| `refresh_token` | Web UI | Rotated on every use, with reuse detection. |
| `client_credentials` | Services | Confidential clients, HTTP Basic. |

```mermaid
sequenceDiagram
    participant B as Browser
    participant A as auth-service
    participant E as Email / Authenticator app
    B->>A: POST /oauth/token (grant=password, email, password)
    A->>A: verify Argon2 hash, lockout counter
    A-->>E: email 6-digit code (email method only)
    A-->>B: 403 mfa_required {mfa_token, mfa_method}
    B->>A: POST /oauth/token (grant=mfa-otp, mfa_token, otp)
    A->>A: verify code (single use, max 5 attempts, 5 min)
    A-->>B: 200 {access_token (15 min), refresh_token (7 d)}
```

MFA is required by default. A new account uses emailed codes, or an authenticator app once the user
enrols one (scan QR, confirm a code). Switching back to email requires the password; an administrator
can reset a lost device.

An administrator can make MFA optional for an individual user (`mfa_required=false`):

- Created as optional, the account starts with no second factor and signs in with the password alone.
- Made optional later, the account keeps its current second factor until the user turns it off
  (password confirmation needed). The user can turn one back on at any time.
- Made required again, an account with no second factor is switched to emailed codes at once.
- Administrators cannot change this setting on their own account.
- Every change is in the activity log, and each successful sign-in records which factor was used
  (`mfa_method: none` for a password-only sign-in), so exempt accounts are easy to find.

Protections in the login path:

- Unknown user and wrong password are indistinguishable (same message, same hashing cost).
- Five consecutive failures lock the account for 15 minutes; an administrator can unlock it.
- Email codes are stored only as a hash bound to their challenge. TOTP secrets are encrypted at rest.
- A TOTP code cannot be replayed: the last accepted time-step is stored.
- Refresh tokens are stored hashed. Presenting an already-rotated refresh token ends that whole session.
- Password change, deactivation and MFA reset end all of the user's sessions at once.
- The gateway rate-limits the token endpoint per IP (container deployment).

Standards endpoints: token (RFC 6749), revocation (RFC 7009), introspection (RFC 7662), server
metadata (RFC 8414), JWKS.

## 3. Authorization (central RBAC)

Model: **user → roles → permissions**. A permission is a string such as `customer:block`. The catalogue
is fixed in code; roles are data and can be created and edited by administrators.

Resource services contain no token validation and no role logic. For each request they call:

```
POST auth-service /api/v1/authz/check      (HTTP Basic: the service's own client id + secret)
{ "token": "<caller's access token>", "permission": "customer:block" }
→ { "active": true, "allowed": false, "principal": { id, type, email, name, roles, permissions } }
```

```mermaid
sequenceDiagram
    participant B as Browser
    participant C as customer-service
    participant A as auth-service
    participant L as audit-service
    B->>C: POST /customers/{id}/block (Bearer token)
    C->>A: authz/check {token, "customer:block"}
    A->>A: signature, expiry, revocation,<br/>user active?, current roles
    A-->>C: active, allowed, principal
    C->>C: update customer + write audit event (one transaction)
    C-->>B: 200
    C-)L: deliver event (background)
```

The auth service reads account state and roles from its database on every check, so the token's own
claims are never trusted for permissions. Consequences:

- Deactivating a user, changing their roles, or editing a role takes effect on the next request, not
  when the token expires. (Covered by tests and the smoke test.)
- If the auth service is unreachable, services answer 503. They fail closed.
- The cost is one internal HTTP call per request. That is the deliberate trade for a basic CRM; if it
  ever matters, the published JWKS allows local signature checks with a short decision cache.

Service scopes (`authz:check`, `audit:write`) exist only on service clients and cannot be granted to
a role, so no user can write to the activity log or call the decision endpoint.

Note: `user:manage` and `role:manage` are administrative powers by nature. Someone holding them can
grant themselves any other permission. They are in the `admin` role only.

## 4. Customer management

| Action | Permission |
|---|---|
| View / search customers, employment, contacts | `customer:read` |
| Add a customer | `customer:create` |
| Edit a customer | `customer:update` |
| Block / unblock | `customer:block` |
| Add, edit, remove employment records | `customer:employment:manage` |
| Add, edit, remove related contacts | `customer:contact:manage` |
| Delete a customer permanently | `customer:delete` (in the `admin` role only by default) |

Rules: email is unique (case-insensitive). Blocking requires a reason and records who and when. A
blocked customer is read-only (the record and its employment/contacts) until unblocked; attempts
return 409, including an attempt to delete it. Deleting removes the customer with its employment
records and contacts; the activity log keeps a copy of the deleted record.

Input rules are defined once in `shared/crm_common/validation.py` and enforced by the services: names
and place names are letters of any script (no digits or symbols), phone numbers have 7 to 15 digits,
dates of birth are between 1900 and today, and a current job cannot have an end date. The web UI
repeats the same rules on its inputs only so the browser can point at the field before sending.

Users can be deleted by anyone holding `user:manage`, except their own account and the last active
administrator. The bootstrap administrator from `.env` is created only while no administrator exists,
so a deleted or renamed one does not come back at the next start.

## 5. Activity logging

Every action produces an event with: time (UTC), service, action, outcome, actor (id, type, email,
name, roles at that moment), record type and id, IP address, user agent, request id, and details
(for edits, each changed field with its old and new value).

What is logged:

| Area | Events |
|---|---|
| Sign-in | challenge issued, MFA failed, login succeeded/failed (with reason), account locked, logout, refresh-token reuse |
| Account | password changed/reset, MFA method changed/reset, unlocked |
| Administration | user created/updated/deleted, role created/updated/deleted |
| Authorization | every denied permission check, with the permission and calling service |
| Customers | searched, viewed, created, updated, blocked, unblocked, deleted; employment and contact viewed/added/updated/removed |
| Activity log | viewing and exporting the log are themselves logged, with the filters used |

Not logged as audit events: reads of the user and role lists in the auth service. Those requests still
appear in the structured access log (below), without the user identity.

Delivery uses a **transactional outbox**. The service writes the event to an `audit_outbox` table in
the same database transaction as the change itself, so a change and its audit record cannot exist
without each other. A background worker delivers batches to the audit service and marks them sent.
Delivery is at-least-once; the audit service ignores an `event_id` it already has. If the audit
service is down, events wait in the outbox (typical delay when healthy: up to 2 seconds).

The audit service exposes no update or delete operations, and records which authenticated service
delivered each event. Anyone with `audit:read` can download the filtered log as CSV (newest first,
capped at 50,000 rows per download; values that a spreadsheet would treat as formulas are neutralised).

Separately, every service writes one JSON log line per request to stdout (method, path, status,
duration, IP, request id). The gateway assigns the request id and it is carried into audit events,
so a log entry can be traced across services.

## 6. Data model

```mermaid
erDiagram
    USERS }o--o{ ROLES : user_roles
    ROLES }o--o{ PERMISSIONS : role_permissions
    USERS ||--o{ REFRESH_TOKENS : has
    USERS ||--o{ MFA_CHALLENGES : has
    USERS {
        uuid id PK
        string email UK
        string password_hash
        bool is_active
        bool mfa_required
        string mfa_method "email | totp | none"
        text totp_secret_enc
        datetime locked_until
        datetime tokens_valid_after
    }
    ROLES { uuid id PK
            string name UK
            bool is_system }
    PERMISSIONS { int id PK
                  string code UK }
    OAUTH_CLIENTS { string client_id PK
                    string secret_hash
                    json grant_types
                    json scopes }
    REFRESH_TOKENS { uuid id PK
                     string token_hash UK
                     uuid family_id
                     datetime revoked_at }
    MFA_CHALLENGES { uuid id PK
                     string token_hash UK
                     string otp_hash
                     int attempts }
```

```mermaid
erDiagram
    CUSTOMERS ||--o{ CUSTOMER_EMPLOYMENTS : has
    CUSTOMERS ||--o{ CUSTOMER_CONTACTS : has
    CUSTOMERS {
        uuid id PK
        string first_name
        string last_name
        string email UK
        string status "active | blocked"
        string block_reason
        datetime blocked_at
        string blocked_by
        string created_by
        string updated_by
    }
    CUSTOMER_EMPLOYMENTS { uuid id PK
                           uuid customer_id FK
                           string employer_name
                           string employment_type
                           decimal annual_income }
    CUSTOMER_CONTACTS { uuid id PK
                        uuid customer_id FK
                        string full_name
                        string relationship_type
                        bool is_emergency_contact }
    AUDIT_EVENTS {
        uuid event_id PK
        datetime occurred_at
        string service
        string action
        string outcome
        string actor_id
        string actor_email
        json actor_roles
        string resource_type
        string resource_id
        string ip_address
        json details
        string submitted_by
    }
```

`audit_events` lives in the audit service's own database. Auth and customer services each also have
an `audit_outbox` table (payload, created_at, sent_at).

## 7. Known limits

These are deliberate scope cuts for a basic system, listed so nobody discovers them later:

- **Password grant, not authorization-code + PKCE.** The UI collects the password and posts it to the
  token endpoint. This is acceptable for a first-party UI on the same origin, and it is what makes the
  two-step MFA flow simple, but OAuth 2.1 drops this grant. A third-party client would need the
  authorization-code flow with a login page hosted by the auth service.
- **Tokens in `sessionStorage`.** The UI keeps tokens in the browser tab. A strict Content-Security-Policy
  (no inline or third-party script) limits the exposure, but an HttpOnly-cookie session at the gateway
  would be stronger.
- **No schema migrations.** Tables are created at startup. This already cost something once: adding
  `users.mfa_required` needed a manual `ALTER TABLE` on the existing local database. Add Alembic.
- **No clean-up job** for expired challenges, refresh tokens, revoked-token ids or sent outbox rows.
- **Signing key is a single file** on a volume. There is no key rotation.
- **Audit log has no tamper-evidence** (hash chain or WORM storage) beyond having no write API for users.
- **Container stack not yet run.** See `docs/DEPLOYMENT.md`.
