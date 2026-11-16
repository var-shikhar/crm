"""Customer service: customer records, employment history and related contacts.

Every endpoint asks the central auth service to authenticate the caller and
authorize the specific action, and every action is written to the activity log.
"""

import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from crm_common.audit import AuditEmitter, AuditShipper
from crm_common.authz import AuthzClient, Principal, ServiceTokenProvider
from crm_common.db import utcnow
from crm_common.web import configure_logging, install_common

from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .models import AuditOutbox, Customer, Employment, RelatedContact
from .schemas import (
    BlockIn,
    ContactCreate,
    ContactOut,
    ContactUpdate,
    CustomerCreate,
    CustomerOut,
    CustomerPage,
    CustomerUpdate,
    EmploymentCreate,
    EmploymentOut,
    EmploymentUpdate,
)

configure_logging(settings.log_level)
authz = AuthzClient(settings.auth_url, settings.client_id, settings.client_secret)
audit = AuditEmitter("customer-service", AuditOutbox)


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)
    shipper = None
    if settings.audit_url:
        tokens = ServiceTokenProvider(settings.auth_url, settings.client_id, settings.client_secret)
        shipper = AuditShipper(SessionLocal, AuditOutbox, settings.audit_url, tokens)
        shipper.start()
    yield
    if shipper:
        shipper.stop()


app = FastAPI(
    title="CRM Customer Service",
    version="1.0.0",
    summary="Customer records with employment history and related contacts.",
    description=(
        "All endpoints require a bearer access token from the auth service. The permission each "
        "endpoint needs is stated in its description. A blocked customer is read-only until unblocked."
    ),
    lifespan=lifespan,
)
install_common(app, "customer-service")


@app.get("/readyz", include_in_schema=False)
def readyz():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(503, "database unavailable")
    return {"status": "ready"}


# ----------------------------------------------------------------- helpers


def _customer(db: Session, customer_id: uuid.UUID) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(404, "Customer not found")
    return customer


def _writable_customer(db: Session, customer_id: uuid.UUID) -> Customer:
    customer = _customer(db, customer_id)
    if customer.status == "blocked":
        raise HTTPException(409, "Customer is blocked; unblock the record before changing it")
    return customer


def _child(db: Session, model, customer_id: uuid.UUID, child_id: uuid.UUID, label: str):
    row = db.get(model, child_id)
    if row is None or row.customer_id != customer_id:
        raise HTTPException(404, f"{label} not found")
    return row


def _apply(row, data: dict) -> dict:
    """Set changed fields on `row`; return {field: {from, to}} for the activity log."""
    changes = {}
    columns = type(row).__table__.columns
    for field, value in data.items():
        if value is None and not columns[field].nullable:
            raise HTTPException(422, f"{field} cannot be null")
        old = getattr(row, field)
        if old != value:
            changes[field] = {"from": old, "to": value}
            setattr(row, field, value)
    return changes


def _ensure_email_free(db: Session, email: str, exclude: uuid.UUID | None = None) -> None:
    stmt = select(Customer.id).where(func.lower(Customer.email) == email.lower())
    if exclude:
        stmt = stmt.where(Customer.id != exclude)
    if db.scalar(stmt):
        raise HTTPException(409, "A customer with this email already exists")


# --------------------------------------------------------------- customers

customers = APIRouter(prefix="/api/v1/customers", tags=["Customers"])


@customers.get("", response_model=CustomerPage, summary="Search customers")
def list_customers(
    request: Request,
    q: str | None = Query(None, description="Matches name, email or phone"),
    status: Literal["active", "blocked"] | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    actor: Principal = Depends(authz.requires("customer:read")),
    db: Session = Depends(get_db),
):
    """Requires `customer:read`."""
    conditions = []
    if q:
        like = f"%{q.strip().lower()}%"
        conditions.append(
            or_(
                func.lower(Customer.first_name + " " + Customer.last_name).like(like),
                func.lower(Customer.email).like(like),
                Customer.phone.like(like),
            )
        )
    if status:
        conditions.append(Customer.status == status)
    total = db.scalar(select(func.count()).select_from(Customer).where(*conditions))
    items = db.scalars(
        select(Customer)
        .where(*conditions)
        .order_by(Customer.last_name, Customer.first_name)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    audit.emit(db, "customer.searched", request=request, actor=actor, resource_type="customer",
               details={"q": q, "status": status, "page": page, "returned": len(items)})
    db.commit()
    return CustomerPage(items=items, total=total, page=page, page_size=page_size)


@customers.post("", response_model=CustomerOut, status_code=201, summary="Add a customer")
def create_customer(body: CustomerCreate, request: Request,
                    actor: Principal = Depends(authz.requires("customer:create")), db: Session = Depends(get_db)):
    """Requires `customer:create`."""
    _ensure_email_free(db, body.email)
    customer = Customer(**body.model_dump(), created_by=actor.id, updated_by=actor.id)
    db.add(customer)
    db.flush()
    audit.emit(db, "customer.created", request=request, actor=actor, resource_type="customer",
               resource_id=customer.id, details={"record": body.model_dump(exclude_none=True)})
    db.commit()
    return customer


@customers.get("/{customer_id}", response_model=CustomerOut, summary="Get a customer")
def get_customer(customer_id: uuid.UUID, request: Request,
                 actor: Principal = Depends(authz.requires("customer:read")), db: Session = Depends(get_db)):
    """Requires `customer:read`."""
    customer = _customer(db, customer_id)
    audit.emit(db, "customer.viewed", request=request, actor=actor, resource_type="customer", resource_id=customer.id)
    db.commit()
    return customer


@customers.patch("/{customer_id}", response_model=CustomerOut, summary="Edit a customer")
def update_customer(customer_id: uuid.UUID, body: CustomerUpdate, request: Request,
                    actor: Principal = Depends(authz.requires("customer:update")), db: Session = Depends(get_db)):
    """Requires `customer:update`. Returns 409 if the customer is blocked."""
    customer = _writable_customer(db, customer_id)
    data = body.model_dump(exclude_unset=True)
    if "email" in data:
        _ensure_email_free(db, data["email"], exclude=customer.id)
    changes = _apply(customer, data)
    if changes:
        customer.updated_by = actor.id
        audit.emit(db, "customer.updated", request=request, actor=actor, resource_type="customer",
                   resource_id=customer.id, details={"changes": changes})
        db.commit()
    return customer


@customers.post("/{customer_id}/block", response_model=CustomerOut, summary="Block a customer")
def block_customer(customer_id: uuid.UUID, body: BlockIn, request: Request,
                   actor: Principal = Depends(authz.requires("customer:block")), db: Session = Depends(get_db)):
    """Requires `customer:block`. A blocked record becomes read-only."""
    customer = _customer(db, customer_id)
    if customer.status == "blocked":
        raise HTTPException(409, "Customer is already blocked")
    customer.status = "blocked"
    customer.block_reason = body.reason
    customer.blocked_at = utcnow()
    customer.blocked_by = actor.id
    customer.updated_by = actor.id
    audit.emit(db, "customer.blocked", request=request, actor=actor, resource_type="customer",
               resource_id=customer.id, details={"reason": body.reason})
    db.commit()
    return customer


@customers.post("/{customer_id}/unblock", response_model=CustomerOut, summary="Unblock a customer")
def unblock_customer(customer_id: uuid.UUID, request: Request,
                     actor: Principal = Depends(authz.requires("customer:block")), db: Session = Depends(get_db)):
    """Requires `customer:block`."""
    customer = _customer(db, customer_id)
    if customer.status != "blocked":
        raise HTTPException(409, "Customer is not blocked")
    previous_reason = customer.block_reason
    customer.status = "active"
    customer.block_reason = None
    customer.blocked_at = None
    customer.blocked_by = None
    customer.updated_by = actor.id
    audit.emit(db, "customer.unblocked", request=request, actor=actor, resource_type="customer",
               resource_id=customer.id, details={"previous_reason": previous_reason})
    db.commit()
    return customer


# -------------------------------------------- employment & related contacts
# The two sub-resources behave identically, so the routes are generated once.


def _register_children(*, path: str, label: str, tag: str, model, permission: str, action: str,
                       create_schema, update_schema, out_schema):
    router = APIRouter(prefix=f"/api/v1/customers/{{customer_id}}/{path}", tags=[tag])
    resource_type = f"customer_{action}"
    a = "an" if label[0] in "aeiou" else "a"

    @router.get("", response_model=list[out_schema], summary=f"List a customer's {label} records")
    def list_children(customer_id: uuid.UUID, request: Request,
                      actor: Principal = Depends(authz.requires("customer:read")), db: Session = Depends(get_db)):
        """Requires `customer:read`."""
        _customer(db, customer_id)
        rows = db.scalars(select(model).where(model.customer_id == customer_id).order_by(model.created_at)).all()
        audit.emit(db, f"customer.{action}.viewed", request=request, actor=actor, resource_type="customer",
                   resource_id=customer_id, details={"returned": len(rows)})
        db.commit()
        return rows

    @router.post("", response_model=out_schema, status_code=201, summary=f"Add {a} {label} record")
    def create_child(customer_id: uuid.UUID, body: create_schema, request: Request,
                     actor: Principal = Depends(authz.requires(permission)), db: Session = Depends(get_db)):
        _writable_customer(db, customer_id)
        row = model(**body.model_dump(), customer_id=customer_id, created_by=actor.id, updated_by=actor.id)
        db.add(row)
        db.flush()
        audit.emit(db, f"customer.{action}.added", request=request, actor=actor, resource_type=resource_type,
                   resource_id=row.id,
                   details={"customer_id": customer_id, "record": body.model_dump(exclude_none=True)})
        db.commit()
        return row

    @router.patch("/{record_id}", response_model=out_schema, summary=f"Edit {a} {label} record")
    def update_child(customer_id: uuid.UUID, record_id: uuid.UUID, body: update_schema, request: Request,
                     actor: Principal = Depends(authz.requires(permission)), db: Session = Depends(get_db)):
        _writable_customer(db, customer_id)
        row = _child(db, model, customer_id, record_id, label.capitalize())
        changes = _apply(row, body.model_dump(exclude_unset=True))
        if changes:
            row.updated_by = actor.id
            audit.emit(db, f"customer.{action}.updated", request=request, actor=actor, resource_type=resource_type,
                       resource_id=row.id, details={"customer_id": customer_id, "changes": changes})
            db.commit()
        return row

    @router.delete("/{record_id}", status_code=204, summary=f"Remove {a} {label} record")
    def delete_child(customer_id: uuid.UUID, record_id: uuid.UUID, request: Request,
                     actor: Principal = Depends(authz.requires(permission)), db: Session = Depends(get_db)):
        _writable_customer(db, customer_id)
        row = _child(db, model, customer_id, record_id, label.capitalize())
        snapshot = out_schema.model_validate(row).model_dump(
            mode="json", exclude={"id", "customer_id", "created_at", "updated_at"}, exclude_none=True
        )
        audit.emit(db, f"customer.{action}.removed", request=request, actor=actor, resource_type=resource_type,
                   resource_id=row.id, details={"customer_id": customer_id, "record": snapshot})
        db.delete(row)
        db.commit()

    for fn in (create_child, update_child, delete_child):
        fn.__doc__ = f"Requires `{permission}`. Returns 409 if the customer is blocked."
    return router


app.include_router(customers)
app.include_router(
    _register_children(
        path="employment", label="employment", tag="Employment", model=Employment, action="employment",
        permission="customer:employment:manage",
        create_schema=EmploymentCreate, update_schema=EmploymentUpdate, out_schema=EmploymentOut,
    )
)
app.include_router(
    _register_children(
        path="contacts", label="related contact", tag="Related contacts", model=RelatedContact, action="contact",
        permission="customer:contact:manage",
        create_schema=ContactCreate, update_schema=ContactUpdate, out_schema=ContactOut,
    )
)
