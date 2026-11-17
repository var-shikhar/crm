"""Idempotent bootstrap data: permission catalogue, default roles, OAuth clients, first admin."""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import OAuthClient, Permission, Role, User
from .security import hash_password, sha256_hex

log = logging.getLogger("auth.seed")

MFA_GRANT = "urn:crm:params:oauth:grant-type:mfa-otp"

# Holding this permission through any role signs the account in with email and password only.
MFA_SKIP = "auth:mfa:skip"

# Permissions that can be granted to human users through roles.
PERMISSIONS: dict[str, str] = {
    "user:read": "View users (administrators only by default)",
    "user:manage": "Create and update users, assign roles, reset MFA",
    "role:read": "View roles and permissions",
    "role:manage": "Create, update and delete roles",
    "customer:read": "View customers and their related records",
    "customer:create": "Add customers",
    "customer:update": "Edit customers",
    "customer:block": "Block and unblock customers",
    "customer:delete": "Permanently delete customers",
    "customer:employment:manage": "Add, edit and remove customer employment records",
    "customer:contact:manage": "Add, edit and remove customer related contacts",
    "audit:read": "View everyone's activity (without it a user sees only their own)",
    MFA_SKIP: "Sign in with email and password only, with no verification code",
}

# The service scopes `authz:check` and `audit:write` are deliberately absent from this catalogue:
# they exist only on the service clients below, so they can never be granted to a role.

# Deleting is permanent, so by default only `admin` holds it; grant it to other roles deliberately.
CUSTOMER_ALL = [p for p in PERMISSIONS if p.startswith("customer:") and p != "customer:delete"]

DEFAULT_ROLES: dict[str, tuple[str, list[str]]] = {
    "admin": ("Full access to every module", list(PERMISSIONS)),
    # Users and other people's activity are for administrators: no `user:read` or `audit:read` here.
    "manager": ("Manages customers including employment and contacts", CUSTOMER_ALL),
    "agent": ("Adds and edits customers", ["customer:read", "customer:create", "customer:update"]),
    "viewer": ("Read-only access to customers", ["customer:read"]),
}


def seed(db: Session) -> None:
    # Permission catalogue
    existing = {p.code: p for p in db.scalars(select(Permission))}
    for code, description in PERMISSIONS.items():
        if code not in existing:
            existing[code] = Permission(code=code, description=description)
            db.add(existing[code])
    db.flush()

    # Roles: created once; later edits by administrators are preserved.
    # `admin` is a system role and always carries every permission.
    for name, (description, codes) in DEFAULT_ROLES.items():
        role = db.scalar(select(Role).where(Role.name == name))
        if role is None:
            role = Role(name=name, description=description, is_system=(name == "admin"))
            role.permissions = [existing[c] for c in codes]
            db.add(role)
        elif name == "admin":
            role.permissions = list(existing.values())
    db.flush()

    # OAuth clients
    clients = [
        # The browser UI: a public client, cannot hold a secret.
        dict(client_id="crm-web", name="CRM Web UI", is_confidential=False,
             grant_types=["password", "refresh_token", MFA_GRANT], scopes=[], secret=None),
        dict(client_id="customer-service", name="Customer Service", is_confidential=True,
             grant_types=["client_credentials"], scopes=["authz:check", "audit:write"],
             secret=settings.customer_service_client_secret),
        dict(client_id="audit-service", name="Audit Service", is_confidential=True,
             grant_types=["client_credentials"], scopes=["authz:check"],
             secret=settings.audit_service_client_secret),
        # The auth service itself, for shipping its own audit events. Tokens are
        # minted in-process, so it has no secret and no grant types.
        dict(client_id="auth-service", name="Auth Service", is_confidential=True,
             grant_types=[], scopes=["audit:write"], secret=None),
    ]
    for spec in clients:
        secret = spec.pop("secret")
        client = db.get(OAuthClient, spec["client_id"])
        if client is None:
            client = OAuthClient(client_id=spec["client_id"])
            db.add(client)
        for key, value in spec.items():
            setattr(client, key, value)
        # Secrets are re-read on every start so rotating the env var rotates the secret.
        client.secret_hash = sha256_hex(secret) if secret else None
        if spec["grant_types"] == ["client_credentials"] and not secret:
            log.warning("client %s has no secret configured and cannot authenticate", spec["client_id"])

    # First administrator: only while the system has no administrator at all. Checking for the
    # bootstrap email alone would bring the account back after it was deleted or renamed.
    email = settings.bootstrap_admin_email.lower()
    admin_role = db.scalar(select(Role).where(Role.name == "admin"))
    has_admin = db.scalar(select(User.id).where(User.roles.any(Role.id == admin_role.id)).limit(1)) is not None
    if (settings.bootstrap_admin_password and not has_admin
            and db.scalar(select(User).where(User.email == email)) is None):
        db.add(
            User(
                email=email,
                full_name=settings.bootstrap_admin_name,
                password_hash=hash_password(settings.bootstrap_admin_password),
                roles=[admin_role],
            )
        )
        log.info("bootstrap administrator created: %s", email)

    db.commit()
