"""OAuth 2.0 endpoints.

Grants:
  password            first-party UI login; answers `mfa_required` (step 1) unless the
                      account is exempt from MFA and has no second factor, or a role
                      grants it `auth:mfa:skip`
  urn:crm:...:mfa-otp completes login with the second factor (step 2)
  refresh_token       rotation with reuse detection
  client_credentials  service-to-service
"""

import uuid
from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from crm_common.db import utcnow

from ..config import settings
from ..db import get_db
from ..deps import audit, parse_basic_auth, resolve_token, service_client, user_principal, verify_client
from ..emailer import EmailDeliveryError, send_otp
from ..models import MfaChallenge, OAuthClient, RefreshToken, RevokedToken, User
from ..schemas import TokenResponse
from ..security import (
    constant_time_equal,
    decrypt,
    keys,
    match_totp,
    mint_access_token,
    new_email_otp,
    new_opaque_token,
    sha256_hex,
    verify_password,
)
from ..seed import MFA_GRANT, MFA_SKIP

router = APIRouter(tags=["OAuth 2.0"])


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status_code: int = 400, **extra):
        self.error = error
        self.description = description
        self.status_code = status_code
        self.extra = extra


def oauth_error_handler(_: Request, exc: OAuthError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "Basic"
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description, **exc.extra},
        status_code=exc.status_code,
        headers=headers,
    )


def _authenticate_client(request: Request, db: Session, client_id: str | None, client_secret: str | None) -> OAuthClient:
    basic = parse_basic_auth(request)
    if basic:
        client_id, client_secret = basic
    client = verify_client(db, client_id, client_secret)
    if client is None:
        raise OAuthError("invalid_client", "Client authentication failed", 401)
    return client


def _issue_user_tokens(db: Session, user: User, client: OAuthClient, family_id: uuid.UUID | None = None) -> TokenResponse:
    access = mint_access_token(
        sub=str(user.id),
        typ="user",
        client_id=client.client_id,
        ttl=settings.access_token_ttl,
        extra={"email": user.email, "name": user.full_name, "roles": user.role_names},
    )
    refresh = new_opaque_token()
    db.add(
        RefreshToken(
            token_hash=sha256_hex(refresh),
            family_id=family_id or uuid.uuid4(),
            user_id=user.id,
            client_id=client.client_id,
            expires_at=utcnow() + timedelta(seconds=settings.refresh_token_ttl),
        )
    )
    return TokenResponse(
        access_token=access,
        expires_in=settings.access_token_ttl,
        refresh_token=refresh,
        scope=" ".join(user.permissions),
    )


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )


def revoke_all_sessions(db: Session, user: User) -> None:
    """Sign the user out everywhere: kill refresh tokens and outstanding access tokens."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    user.tokens_valid_after = utcnow()


# ------------------------------------------------------------------ grants


def _grant_password(request: Request, db: Session, client: OAuthClient, username: str | None, password: str | None):
    if not username or not password:
        raise OAuthError("invalid_request", "username and password are required")
    email = username.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    bad_credentials = OAuthError("invalid_grant", "Invalid email or password")

    def fail(reason: str, error: OAuthError = bad_credentials):
        audit.emit(db, "auth.login.failed", request=request, actor_email=email, outcome="failure",
                   resource_type="user", resource_id=user.id if user else None, details={"reason": reason})
        db.commit()
        raise error

    if user is not None and user.is_locked:
        fail("account_locked", OAuthError("invalid_grant", "Account is temporarily locked. Try again later."))

    if not verify_password(user.password_hash if user else None, password):
        if user is None:
            fail("unknown_user")
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.failed_login_count = 0
            user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
            audit.emit(db, "auth.account.locked", request=request, actor_email=email, resource_type="user",
                       resource_id=user.id, details={"minutes": settings.lockout_minutes})
        fail("bad_password")

    if not user.is_active:
        fail("account_disabled", OAuthError("invalid_grant", "Account is disabled"))

    user.failed_login_count = 0
    user.locked_until = None

    if user.mfa_method == "none" or user.mfa_skipped:
        # No second step: either the account is exempt from MFA (administrator's decision) and has
        # not opted in, or one of its roles grants `auth:mfa:skip`. The log records which.
        user.last_login_at = utcnow()
        tokens = _issue_user_tokens(db, user, client)
        details = {"mfa_method": "none", "client_id": client.client_id}
        if user.mfa_skipped:
            details["mfa_skipped_by"] = MFA_SKIP
        audit.emit(db, "auth.login.succeeded", request=request, actor=user_principal(user), resource_type="user",
                   resource_id=user.id, details=details)
        db.commit()
        return tokens

    # First factor passed. Open a second-factor challenge.
    mfa_token = new_opaque_token()
    challenge = MfaChallenge(
        token_hash=sha256_hex(mfa_token),
        user_id=user.id,
        client_id=client.client_id,
        method=user.mfa_method,
        expires_at=utcnow() + timedelta(seconds=settings.mfa_challenge_ttl),
    )
    if user.mfa_method == "email":
        code = new_email_otp()
        challenge.otp_hash = sha256_hex(f"{challenge.token_hash}:{code}")
        try:
            send_otp(user.email, code)
        except EmailDeliveryError:
            raise OAuthError("temporarily_unavailable", "Could not send the verification email", 503)
    db.add(challenge)
    audit.emit(db, "auth.mfa.challenged", request=request, actor=user_principal(user), resource_type="user",
               resource_id=user.id, details={"method": user.mfa_method})
    db.commit()
    raise OAuthError(
        "mfa_required",
        "Enter the code from your authenticator app" if user.mfa_method == "totp"
        else "Enter the verification code sent to your email",
        403,
        mfa_token=mfa_token,
        mfa_method=user.mfa_method,
        expires_in=settings.mfa_challenge_ttl,
    )


def _grant_mfa(request: Request, db: Session, client: OAuthClient, mfa_token: str | None, otp: str | None):
    if not mfa_token or not otp:
        raise OAuthError("invalid_request", "mfa_token and otp are required")
    challenge = db.scalar(select(MfaChallenge).where(MfaChallenge.token_hash == sha256_hex(mfa_token)))
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= utcnow()
        or challenge.client_id != client.client_id
        or challenge.attempts >= settings.mfa_max_attempts
    ):
        raise OAuthError("invalid_grant", "Verification expired. Sign in again.")
    user = challenge.user
    if not user.is_active:
        raise OAuthError("invalid_grant", "Account is disabled")

    ok = False
    if challenge.method == "totp" and user.totp_secret_enc:
        counter = match_totp(decrypt(user.totp_secret_enc), otp, user.totp_last_counter)
        if counter is not None:
            user.totp_last_counter = counter
            ok = True
    elif challenge.method == "email" and challenge.otp_hash:
        ok = constant_time_equal(challenge.otp_hash, sha256_hex(f"{challenge.token_hash}:{otp.strip()}"))

    if not ok:
        challenge.attempts += 1
        audit.emit(db, "auth.mfa.failed", request=request, actor=user_principal(user), outcome="failure",
                   resource_type="user", resource_id=user.id,
                   details={"method": challenge.method, "attempt": challenge.attempts})
        db.commit()
        raise OAuthError("invalid_grant", "Invalid verification code")

    challenge.consumed_at = utcnow()
    user.last_login_at = utcnow()
    tokens = _issue_user_tokens(db, user, client)
    audit.emit(db, "auth.login.succeeded", request=request, actor=user_principal(user), resource_type="user",
               resource_id=user.id, details={"mfa_method": challenge.method, "client_id": client.client_id})
    db.commit()
    return tokens


def _grant_refresh(request: Request, db: Session, client: OAuthClient, refresh_token: str | None):
    if not refresh_token:
        raise OAuthError("invalid_request", "refresh_token is required")
    invalid = OAuthError("invalid_grant", "Session expired. Sign in again.")
    stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(refresh_token)))
    if stored is None or stored.client_id != client.client_id:
        raise invalid
    user = stored.user
    if stored.revoked_at is not None:
        # A rotated token was presented again: assume theft and end the whole session.
        _revoke_family(db, stored.family_id)
        audit.emit(db, "auth.refresh.reuse_detected", request=request, actor=user_principal(user),
                   outcome="failure", resource_type="user", resource_id=user.id)
        db.commit()
        raise invalid
    if stored.expires_at <= utcnow() or not user.is_active:
        raise invalid
    stored.revoked_at = utcnow()
    tokens = _issue_user_tokens(db, user, client, family_id=stored.family_id)
    db.commit()
    return tokens


def _grant_client_credentials(db: Session, client: OAuthClient):
    if not client.is_confidential:
        raise OAuthError("unauthorized_client", "Public clients cannot use client_credentials")
    access = mint_access_token(sub=client.client_id, typ="service", client_id=client.client_id,
                               ttl=settings.service_token_ttl)
    return TokenResponse(access_token=access, expires_in=settings.service_token_ttl, scope=" ".join(client.scopes))


# --------------------------------------------------------------- endpoints


@router.post(
    "/oauth/token",
    response_model=TokenResponse,
    response_model_exclude_none=True,
    summary="Token endpoint",
    responses={
        400: {"description": "OAuth error (`invalid_grant`, `invalid_request`, `unsupported_grant_type`, ...)"},
        401: {"description": "`invalid_client`"},
        403: {"description": "`mfa_required` (password grant, account has a second factor and no `auth:mfa:skip` permission): body carries `mfa_token` and `mfa_method`; "
                             "retry with the MFA grant and the one-time code"},
    },
)
def token(
    request: Request,
    grant_type: str = Form(description=f"`password`, `{MFA_GRANT}`, `refresh_token` or `client_credentials`"),
    username: str | None = Form(None, description="User email (password grant)"),
    password: str | None = Form(None),
    mfa_token: str | None = Form(None, description="From the `mfa_required` response (MFA grant)"),
    otp: str | None = Form(None, description="6-digit code from email or authenticator app (MFA grant)"),
    refresh_token: str | None = Form(None),
    client_id: str | None = Form(None, description="Public clients; confidential clients use HTTP Basic"),
    client_secret: str | None = Form(None),
    db: Session = Depends(get_db),
):
    client = _authenticate_client(request, db, client_id, client_secret)
    if grant_type not in ("password", MFA_GRANT, "refresh_token", "client_credentials"):
        raise OAuthError("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")
    if grant_type not in client.grant_types:
        raise OAuthError("unauthorized_client", "This client may not use that grant type")

    if grant_type == "password":
        result = _grant_password(request, db, client, username, password)
    elif grant_type == MFA_GRANT:
        result = _grant_mfa(request, db, client, mfa_token, otp)
    elif grant_type == "refresh_token":
        result = _grant_refresh(request, db, client, refresh_token)
    else:
        result = _grant_client_credentials(db, client)
    return JSONResponse(result.model_dump(exclude_none=True), headers={"Cache-Control": "no-store"})


@router.post("/oauth/revoke", summary="Revoke a token (RFC 7009) - sign out")
def revoke(
    request: Request,
    token: str = Form(description="A refresh token or an access token"),
    client_id: str | None = Form(None),
    client_secret: str | None = Form(None),
    db: Session = Depends(get_db),
):
    """Revoking a refresh token ends that whole login session. Always answers 200."""
    client = _authenticate_client(request, db, client_id, client_secret)
    stored = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == sha256_hex(token)))
    if stored is not None and stored.client_id == client.client_id:
        _revoke_family(db, stored.family_id)
        audit.emit(db, "auth.logout", request=request, actor=user_principal(stored.user),
                   resource_type="user", resource_id=stored.user_id)
    else:
        try:
            claims = keys.verify(token)
        except jwt.PyJWTError:
            claims = None
        if claims and claims.get("client_id") == client.client_id and db.get(RevokedToken, claims["jti"]) is None:
            db.add(RevokedToken(jti=claims["jti"], expires_at=utcnow() + timedelta(seconds=settings.access_token_ttl)))
    db.commit()
    return {}


@router.post("/oauth/introspect", summary="Token introspection (RFC 7662)")
def introspect(
    token: str = Form(),
    _: OAuthClient = Depends(service_client("authz:check")),
    db: Session = Depends(get_db),
):
    """For confidential service clients (HTTP Basic). Reflects live account state."""
    principal = resolve_token(db, token)
    if principal is None:
        return {"active": False}
    claims = keys.verify(token)
    return {
        "active": True,
        "sub": principal.id,
        "token_type": "Bearer",
        "typ": principal.type,
        "client_id": claims["client_id"],
        "username": principal.email,
        "name": principal.name,
        "roles": principal.roles,
        "scope": " ".join(principal.permissions),
        "iss": claims["iss"],
        "aud": claims["aud"],
        "iat": claims["iat"],
        "exp": claims["exp"],
        "jti": claims["jti"],
    }


@router.get("/.well-known/jwks.json", summary="Public signing keys (JWKS)")
def jwks():
    return keys.jwks()


@router.get("/.well-known/oauth-authorization-server", summary="Authorization server metadata (RFC 8414)")
def metadata():
    base = settings.issuer
    return {
        "issuer": base,
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "introspection_endpoint": f"{base}/oauth/introspect",
        "jwks_uri": f"{base}/.well-known/jwks.json",
        "grant_types_supported": ["password", MFA_GRANT, "refresh_token", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "response_types_supported": [],
        "id_token_signing_alg_values_supported": ["RS256"],
    }
