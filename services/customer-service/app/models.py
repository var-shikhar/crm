import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from crm_common.audit import define_outbox
from crm_common.db import UTCDateTime, utcnow

from .db import Base


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100), index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="active", index=True)  # active | blocked
    block_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    blocked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_by: Mapped[str] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    employments: Mapped[list["Employment"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", order_by="Employment.created_at"
    )
    contacts: Mapped[list["RelatedContact"]] = relationship(
        back_populates="customer", cascade="all, delete-orphan", order_by="RelatedContact.created_at"
    )


class Employment(Base):
    __tablename__ = "customer_employments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    employer_name: Mapped[str] = mapped_column(String(200))
    job_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    employment_type: Mapped[str] = mapped_column(String(32), default="full_time")
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    annual_income: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="employments")


class RelatedContact(Base):
    __tablename__ = "customer_contacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    relationship_type: Mapped[str] = mapped_column(String(64))  # spouse, parent, colleague, ...
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_emergency_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64))
    updated_by: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    customer: Mapped[Customer] = relationship(back_populates="contacts")


AuditOutbox = define_outbox(Base)
