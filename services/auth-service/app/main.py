from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from crm_common.audit import AuditShipper
from crm_common.web import configure_logging, install_common

from .config import settings
from .db import Base, SessionLocal, engine
from .deps import mint_internal_service_token
from .models import AuditOutbox
from .routers import admin, authz, me, oauth
from .seed import seed

configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed(db)
    shipper = None
    if settings.audit_url:
        shipper = AuditShipper(SessionLocal, AuditOutbox, settings.audit_url, mint_internal_service_token)
        shipper.start()
    yield
    if shipper:
        shipper.stop()


app = FastAPI(
    title="CRM Auth Service",
    version="1.0.0",
    summary="Central identity, OAuth 2.0 token service, MFA and role-based access control.",
    description=(
        "Issues and validates tokens, manages users, roles and permissions, and makes every "
        "authorization decision for the other CRM services via `POST /api/v1/authz/check`."
    ),
    lifespan=lifespan,
)
install_common(app, "auth-service")
app.add_exception_handler(oauth.OAuthError, oauth.oauth_error_handler)

app.include_router(oauth.router)
app.include_router(authz.router)
app.include_router(me.router)
app.include_router(admin.users)
app.include_router(admin.roles)


@app.get("/readyz", include_in_schema=False)
def readyz():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ready"}
