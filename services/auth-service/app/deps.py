import base64
import binascii
import uuid

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from crm_common.audit import AuditEmitter
from crm_common.authz import Principal

from .config import settings
from .db import get_db
from .models import AuditOutbox, OAuthClient, RevokedToken, User
from .security import constant_time_equal, keys, mint_access_token, sha256_hex

audit = AuditEmitter("auth-service", AuditOutbox)
_bearer = HTTPBearer(auto_error=False)


def user_principal(user: User) -> Principal:
    return Principal(
        id=str(user.id),
        type="user",
        email=user.email,
        name=user.full_name,
        roles=user.role_names,
        permissions=user.permissions,
    )


def resolve_token(db: Session, token: str) -> Principal | None:
    """Validate an access token against *current* state and return its owner.

    Signature and expiry come from the JWT; everything else (revocation, account
    status, roles, permissions) is read fresh from the database, so changes take
    effect on the very next request rather than when the token expires.
    """
    try:
        claims = keys.verify(token)
    except jwt.PyJWTError:
        return None
    if db.get(RevokedToken, claims["jti"]) is not None:
        return None

    if claims.get("typ") == "user":
        try:
            user = db.get(User, uuid.UUID(claims["sub"]))
        except ValueError:
            return None
        if user is None or not user.is_active:
            return None
        if user.tokens_valid_after and claims["iat"] < int(user.tokens_valid_after.timestamp()):
            return None
        return user_principal(user)

    if claims.get("typ") == "service":
        client = db.get(OAuthClient, claims["sub"])
        if client is None or not client.is_active:
            return None
        return Principal(id=client.client_id, type="service", name=client.name, permissions=list(client.scopes))

    return None


def require(permission: str | None = None):
    """Dependency for the auth service's own endpoints."""

    def dependency(
        request: Request,
        creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
        db: Session = Depends(get_db),
    ) -> Principal:
        principal = resolve_token(db, creds.credentials) if creds else None
        if principal is None:
            raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
        if permission and not principal.has(permission):
            audit.emit(db, "authz.denied", request=request, actor=principal, outcome="denied",
                       details={"permission": permission, "service": "auth-service"})
            db.commit()
            raise HTTPException(403, f"Missing permission: {permission}")
        return principal

    return dependency


def current_user(
    principal: Principal = Depends(require()),
    db: Session = Depends(get_db),
) -> User:
    if principal.type != "user":
        raise HTTPException(403, "This endpoint is for user accounts")
    return db.get(User, uuid.UUID(principal.id))


def parse_basic_auth(request: Request) -> tuple[str, str] | None:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("basic "):
        return None
    try:
        client_id, _, secret = base64.b64decode(header[6:]).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return client_id, secret


def verify_client(db: Session, client_id: str | None, secret: str | None) -> OAuthClient | None:
    client = db.get(OAuthClient, client_id) if client_id else None
    if client is None or not client.is_active:
        return None
    if client.is_confidential:
        if not secret or not client.secret_hash:
            return None
        if not constant_time_equal(client.secret_hash, sha256_hex(secret)):
            return None
    return client


def service_client(scope: str):
    """Dependency: caller must be a confidential client (HTTP Basic) holding `scope`."""

    def dependency(request: Request, db: Session = Depends(get_db)) -> OAuthClient:
        creds = parse_basic_auth(request)
        client = verify_client(db, *creds) if creds else None
        if client is None or not client.is_confidential:
            raise HTTPException(401, "Client authentication failed", headers={"WWW-Authenticate": "Basic"})
        if scope not in client.scopes:
            raise HTTPException(403, f"Client lacks scope: {scope}")
        return client

    return dependency


def mint_internal_service_token() -> str:
    """Token the auth service uses to deliver its own audit events."""
    return mint_access_token(sub="auth-service", typ="service", client_id="auth-service",
                             ttl=settings.service_token_ttl)
