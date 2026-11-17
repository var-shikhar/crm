import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, ForeignKey, Integer, String, Table, Text, Uuid, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from crm_common.audit import define_outbox
from crm_common.db import UTCDateTime, utcnow

from .db import Base

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)


class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)  # system roles are immutable
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    permissions: Mapped[list[Permission]] = relationship(secondary=role_permissions, order_by=Permission.code)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Second factor: "email" (default), "totp", or "none".
    # An administrator decides per user whether one is required; "none" is only
    # possible while mfa_required is false.
    mfa_required: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    mfa_method: Mapped[str] = mapped_column(String(16), default="email")
    totp_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_pending_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_last_counter: Mapped[int] = mapped_column(Integer, default=0)  # blocks code replay

    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Access tokens issued before this instant are rejected (password change, deactivation, ...).
    tokens_valid_after: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    roles: Mapped[list[Role]] = relationship(secondary=user_roles, order_by=Role.name)

    @property
    def role_names(self) -> list[str]:
        return [r.name for r in self.roles]

    @property
    def permissions(self) -> list[str]:
        return sorted({p.code for r in self.roles for p in r.permissions})

    @property
    def mfa_skipped(self) -> bool:
        """True when a role grants sign-in without a code, whatever second factor is configured."""
        return any(p.code == "auth:mfa:skip" for r in self.roles for p in r.permissions)

    @property
    def is_locked(self) -> bool:
        return self.locked_until is not None and self.locked_until > utcnow()


class OAuthClient(Base):
    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_confidential: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    grant_types: Mapped[list] = mapped_column(JSON, default=list)
    scopes: Mapped[list] = mapped_column(JSON, default=list)  # permissions held by a service client


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)  # one login session
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    user: Mapped[User] = relationship()


class MfaChallenge(Base):
    __tablename__ = "mfa_challenges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    client_id: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(16))
    otp_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # email codes only
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    user: Mapped[User] = relationship()


class RevokedToken(Base):
    """Access tokens revoked before their natural expiry (logout)."""

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


AuditOutbox = define_outbox(Base)
