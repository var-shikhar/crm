"""Audit service: the append-only activity log for the whole CRM.

Other services deliver events here (permission `audit:write`, held only by
service clients). Events are never updated or deleted through the API.
"""

import csv
import io
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, String, Uuid, and_, func, or_, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from crm_common.authz import AuthzClient, Principal
from crm_common.db import UTCDateTime, utcnow
from crm_common.web import configure_logging, install_common, request_meta

from .config import settings
from .db import Base, engine, get_db

configure_logging(settings.log_level)
authz = AuthzClient(settings.auth_url, settings.client_id, settings.client_secret)


# ------------------------------------------------------------------- model


class AuditEvent(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    service: Mapped[str] = mapped_column(String(64), index=True)
    submitted_by: Mapped[str] = mapped_column(String(64))  # authenticated client that delivered the event
    action: Mapped[str] = mapped_column(String(100), index=True)
    outcome: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_email: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    actor_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    actor_roles: Mapped[list] = mapped_column(JSON, default=list)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


# ----------------------------------------------------------------- schemas


class EventIn(BaseModel):
    event_id: uuid.UUID
    occurred_at: datetime
    service: str = Field(max_length=64)
    action: str = Field(max_length=100, examples=["customer.blocked"])
    outcome: str = Field(default="success", max_length=16, description="success | failure | denied")
    actor_id: str | None = Field(default=None, max_length=64)
    actor_type: str = Field(default="user", max_length=16, description="user | service | anonymous")
    actor_email: str | None = Field(default=None, max_length=255)
    actor_name: str | None = Field(default=None, max_length=200)
    actor_roles: list[str] = []
    resource_type: str | None = Field(default=None, max_length=64)
    resource_id: str | None = Field(default=None, max_length=64)
    ip_address: str | None = Field(default=None, max_length=64)
    user_agent: str | None = Field(default=None, max_length=300)
    request_id: str | None = Field(default=None, max_length=64)
    details: dict = {}


class EventOut(EventIn):
    model_config = ConfigDict(from_attributes=True)

    received_at: datetime
    submitted_by: str


class BatchIn(BaseModel):
    events: list[EventIn] = Field(min_length=1, max_length=500)


class BatchOut(BaseModel):
    accepted: int
    duplicates: int


class EventPage(BaseModel):
    items: list[EventOut]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------- app


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(
    title="CRM Audit Service",
    version="1.0.0",
    summary="Append-only activity log: who did what, to what, when, and from where.",
    lifespan=lifespan,
)
install_common(app, "audit-service")


@app.get("/readyz", include_in_schema=False)
def readyz():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ready"}


@app.post("/api/v1/events/batch", response_model=BatchOut, status_code=202, tags=["Ingest (service-to-service)"],
          summary="Deliver activity events")
def ingest(body: BatchIn, caller: Principal = Depends(authz.requires("audit:write")), db: Session = Depends(get_db)):
    """Idempotent: an `event_id` that was already stored is counted as a duplicate and skipped,
    so senders can safely retry."""
    ids = [e.event_id for e in body.events]
    seen = set(db.scalars(select(AuditEvent.event_id).where(AuditEvent.event_id.in_(ids))))
    accepted = 0
    for event in body.events:
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        db.add(AuditEvent(**event.model_dump(), submitted_by=caller.id))
        accepted += 1
    db.commit()
    return BatchOut(accepted=accepted, duplicates=len(body.events) - accepted)


def event_filters(
    actor_id: str | None = None,
    actor_email: str | None = Query(None, description="Substring match"),
    action: str | None = Query(None, description="Exact action, or a prefix ending in `*` (e.g. `customer.*`)"),
    service: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str | None = None,
    occurred_from: datetime | None = Query(None, alias="from"),
    occurred_to: datetime | None = Query(None, alias="to"),
) -> list:
    """Shared by search and export, so both always select the same events."""
    conditions = []
    if actor_id:
        conditions.append(AuditEvent.actor_id == actor_id)
    if actor_email:
        conditions.append(func.lower(AuditEvent.actor_email).like(f"%{actor_email.lower()}%"))
    if action:
        conditions.append(AuditEvent.action.like(action[:-1] + "%") if action.endswith("*") else AuditEvent.action == action)
    if service:
        conditions.append(AuditEvent.service == service)
    if resource_type:
        conditions.append(AuditEvent.resource_type == resource_type)
    if resource_id:
        conditions.append(AuditEvent.resource_id == resource_id)
    if outcome:
        conditions.append(AuditEvent.outcome == outcome)
    if occurred_from:
        conditions.append(AuditEvent.occurred_at >= occurred_from)
    if occurred_to:
        conditions.append(AuditEvent.occurred_at <= occurred_to)
    return conditions


READ_ALL = "audit:read"


def reader(request: Request, caller: Principal = Depends(authz.requires(None))) -> Principal:
    """Who may read the log: holders of `audit:read` (everything) and any signed-in user (own account)."""
    if caller.type != "user" and not caller.has(READ_ALL):
        raise HTTPException(403, f"Missing permission: {READ_ALL}")
    return caller


def visible_to(caller: Principal) -> list:
    """Row-level rule. With `audit:read`: every event. Otherwise only the caller's own account:
    what they did, and what was done to their user record (sign-in attempts, changes by an administrator)."""
    if caller.has(READ_ALL):
        return []
    return [or_(AuditEvent.actor_id == caller.id,
                and_(AuditEvent.resource_type == "user", AuditEvent.resource_id == caller.id))]


def record_access(db: Session, request: Request, caller: Principal, action: str, returned: int) -> None:
    """Reading the log is itself an audited action."""
    meta = request_meta(request)
    db.add(
        AuditEvent(
            event_id=uuid.uuid4(), occurred_at=utcnow(), service="audit-service", submitted_by="audit-service",
            action=action, outcome="success", actor_id=caller.id, actor_type=caller.type,
            actor_email=caller.email, actor_name=caller.name, actor_roles=caller.roles,
            resource_type="audit_log", ip_address=meta["ip_address"], user_agent=meta["user_agent"],
            request_id=meta["request_id"],
            details={"filters": dict(request.query_params), "returned": returned,
                     "scope": "all" if caller.has(READ_ALL) else "own"},
        )
    )
    db.commit()


@app.get("/api/v1/events", response_model=EventPage, tags=["Activity log"], summary="Search the activity log")
def list_events(
    request: Request,
    conditions: list = Depends(event_filters),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    caller: Principal = Depends(reader),
    db: Session = Depends(get_db),
):
    """Newest first. Viewing the log is itself recorded.

    With `audit:read` you see everyone's activity. Without it you see only your own account's:
    your actions, and actions on your user record."""
    conditions = conditions + visible_to(caller)
    total = db.scalar(select(func.count()).select_from(AuditEvent).where(*conditions))
    items = db.scalars(
        select(AuditEvent)
        .where(*conditions)
        .order_by(AuditEvent.occurred_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    result = EventPage(items=items, total=total, page=page, page_size=page_size)
    record_access(db, request, caller, "audit.viewed", len(items))
    return result


EXPORT_COLUMNS = [
    "occurred_at", "service", "action", "outcome", "actor_email", "actor_name", "actor_id", "actor_type",
    "actor_roles", "resource_type", "resource_id", "ip_address", "user_agent", "request_id", "details",
]
EXPORT_MAX_ROWS = 50_000


def _csv_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return ", ".join(map(str, value))
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True) if value else ""
    text_value = str(value)
    # Stop spreadsheet programs from running a cell as a formula.
    return "'" + text_value if text_value[:1] in ("=", "+", "-", "@", "\t", "\r") else text_value


@app.get("/api/v1/events/export", tags=["Activity log"], summary="Download the activity log as CSV",
         response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def export_events(
    request: Request,
    conditions: list = Depends(event_filters),
    caller: Principal = Depends(reader),
    db: Session = Depends(get_db),
):
    """Same filters and the same visibility rule as the search. Newest first, at most 50,000 rows;
    the `X-Truncated` header is `true` when more matched. The export is itself recorded, with the filters used."""
    conditions = conditions + visible_to(caller)
    rows = db.scalars(
        select(AuditEvent).where(*conditions).order_by(AuditEvent.occurred_at.desc()).limit(EXPORT_MAX_ROWS + 1)
    ).all()
    truncated = len(rows) > EXPORT_MAX_ROWS
    rows = rows[:EXPORT_MAX_ROWS]
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([_csv_cell(getattr(row, column)) for column in EXPORT_COLUMNS])
    record_access(db, request, caller, "audit.exported", len(rows))
    filename = f"activity-log-{utcnow():%Y%m%d-%H%M%S}.csv"
    return Response(
        buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"',
                 "X-Truncated": "true" if truncated else "false", "Cache-Control": "no-store"},
    )


@app.get("/api/v1/events/{event_id}", response_model=EventOut, tags=["Activity log"], summary="Get one event")
def get_event(event_id: uuid.UUID, caller: Principal = Depends(reader), db: Session = Depends(get_db)):
    """An event outside what the caller may see answers 404, the same as one that does not exist."""
    event = db.scalar(select(AuditEvent).where(AuditEvent.event_id == event_id, *visible_to(caller)))
    if event is None:
        raise HTTPException(404, "Event not found")
    return event
