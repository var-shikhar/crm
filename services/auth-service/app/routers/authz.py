from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import audit, resolve_token, service_client
from ..models import OAuthClient
from ..schemas import AuthzCheckIn, AuthzCheckOut, PrincipalOut

router = APIRouter(prefix="/api/v1/authz", tags=["Authorization (service-to-service)"])


@router.post("/check", response_model=AuthzCheckOut, summary="Authenticate a token and authorize one action")
def check(
    body: AuthzCheckIn,
    client: OAuthClient = Depends(service_client("authz:check")),
    db: Session = Depends(get_db),
):
    """The single decision point every other service calls before acting.

    Caller authenticates with HTTP Basic (its client id and secret).
    `active=false` means the token is not valid (expired, revoked, user disabled);
    `allowed=false` means the token is valid but its owner lacks `permission`.
    Denials are recorded in the activity log.
    """
    principal = resolve_token(db, body.token)
    if principal is None:
        return AuthzCheckOut(active=False, allowed=False)
    allowed = principal.has(body.permission)
    if not allowed:
        audit.emit(
            db,
            "authz.denied",
            actor=principal,
            outcome="denied",
            details={"permission": body.permission, "service": client.client_id, **body.context},
        )
        db.commit()
    return AuthzCheckOut(active=True, allowed=allowed, principal=PrincipalOut(**principal.as_dict()))
