"""User and role administration (RBAC management)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from crm_common.authz import Principal

from ..db import get_db
from ..deps import audit, require
from ..models import MfaChallenge, Permission, RefreshToken, Role, User, user_roles
from ..schemas import PermissionOut, RoleCreate, RoleOut, RoleUpdate, SetPassword, UserCreate, UserOut, UserUpdate
from ..security import hash_password
from .oauth import revoke_all_sessions

users = APIRouter(prefix="/api/v1/users", tags=["Users"])
roles = APIRouter(prefix="/api/v1", tags=["Roles & permissions"])


def _get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    return user


def _resolve_roles(db: Session, names: list[str]) -> list[Role]:
    found = db.scalars(select(Role).where(Role.name.in_(names))).all() if names else []
    missing = set(names) - {r.name for r in found}
    if missing:
        raise HTTPException(422, f"Unknown role(s): {', '.join(sorted(missing))}")
    return list(found)


def _resolve_permissions(db: Session, codes: list[str]) -> list[Permission]:
    found = db.scalars(select(Permission).where(Permission.code.in_(codes))).all() if codes else []
    missing = set(codes) - {p.code for p in found}
    if missing:
        raise HTTPException(422, f"Unknown permission(s): {', '.join(sorted(missing))}")
    return list(found)


def _role_out(role: Role) -> RoleOut:
    return RoleOut(id=role.id, name=role.name, description=role.description, is_system=role.is_system,
                   permissions=[p.code for p in role.permissions])


# -------------------------------------------------------------------- users


@users.get("", response_model=list[UserOut], summary="List users")
def list_users(
    q: str | None = Query(None, description="Search name or email"),
    _: Principal = Depends(require("user:read")),
    db: Session = Depends(get_db),
):
    stmt = select(User).order_by(User.email)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.full_name).like(like)))
    return db.scalars(stmt).all()


@users.post("", response_model=UserOut, status_code=201, summary="Create a user")
def create_user(body: UserCreate, request: Request, actor: Principal = Depends(require("user:manage")),
                db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "A user with this email already exists")
    user = User(email=email, full_name=body.full_name, password_hash=hash_password(body.password),
                roles=_resolve_roles(db, body.roles), mfa_required=body.mfa_required,
                mfa_method="email" if body.mfa_required else "none")
    db.add(user)
    db.flush()
    audit.emit(db, "user.created", request=request, actor=actor, resource_type="user", resource_id=user.id,
               details={"email": email, "full_name": body.full_name, "roles": body.roles,
                        "mfa_required": body.mfa_required})
    db.commit()
    return user


@users.get("/{user_id}", response_model=UserOut, summary="Get a user")
def get_user(user_id: uuid.UUID, _: Principal = Depends(require("user:read")), db: Session = Depends(get_db)):
    return _get_user(db, user_id)


@users.patch("/{user_id}", response_model=UserOut, summary="Update name, status, roles or MFA requirement")
def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request,
                actor: Principal = Depends(require("user:manage")), db: Session = Depends(get_db)):
    """Role and status changes apply to the user's very next request.

    You cannot deactivate yourself or change your own roles or MFA requirement.
    Making MFA required for a user who has none switches them to email codes;
    making it optional leaves their current second factor in place until they turn it off.
    """
    user = _get_user(db, user_id)
    is_self = str(user.id) == actor.id
    changes: dict = {}

    if body.full_name is not None and body.full_name != user.full_name:
        changes["full_name"] = {"from": user.full_name, "to": body.full_name}
        user.full_name = body.full_name

    if body.roles is not None and set(body.roles) != set(user.role_names):
        if is_self:
            raise HTTPException(400, "You cannot change your own roles")
        changes["roles"] = {"from": user.role_names, "to": sorted(body.roles)}
        user.roles = _resolve_roles(db, body.roles)

    if body.is_active is not None and body.is_active != user.is_active:
        if is_self:
            raise HTTPException(400, "You cannot deactivate your own account")
        changes["is_active"] = {"from": user.is_active, "to": body.is_active}
        user.is_active = body.is_active
        if not body.is_active:
            revoke_all_sessions(db, user)

    if body.mfa_required is not None and body.mfa_required != user.mfa_required:
        if is_self:
            raise HTTPException(400, "You cannot change your own MFA requirement")
        changes["mfa_required"] = {"from": user.mfa_required, "to": body.mfa_required}
        user.mfa_required = body.mfa_required
        if body.mfa_required and user.mfa_method == "none":
            changes["mfa_method"] = {"from": "none", "to": "email"}
            user.mfa_method = "email"

    if changes:
        audit.emit(db, "user.updated", request=request, actor=actor, resource_type="user", resource_id=user.id,
                   details={"email": user.email, "changes": changes})
        db.commit()
    return user


@users.post("/{user_id}/password", status_code=204, summary="Set a new password for a user")
def set_password(user_id: uuid.UUID, body: SetPassword, request: Request,
                 actor: Principal = Depends(require("user:manage")), db: Session = Depends(get_db)):
    """Signs the user out of every session."""
    user = _get_user(db, user_id)
    user.password_hash = hash_password(body.new_password)
    revoke_all_sessions(db, user)
    audit.emit(db, "user.password.reset", request=request, actor=actor, resource_type="user", resource_id=user.id,
               details={"email": user.email})
    db.commit()


@users.post("/{user_id}/mfa/reset", response_model=UserOut, summary="Reset a user's second factor to email")
def reset_mfa(user_id: uuid.UUID, request: Request, actor: Principal = Depends(require("user:manage")),
              db: Session = Depends(get_db)):
    """For a lost authenticator device. Removes the TOTP secret and signs the user out."""
    user = _get_user(db, user_id)
    user.mfa_method = "email"
    user.totp_secret_enc = None
    user.totp_pending_secret_enc = None
    revoke_all_sessions(db, user)
    audit.emit(db, "user.mfa.reset", request=request, actor=actor, resource_type="user", resource_id=user.id,
               details={"email": user.email})
    db.commit()
    return user


@users.post("/{user_id}/unlock", response_model=UserOut, summary="Clear a failed-login lockout")
def unlock(user_id: uuid.UUID, request: Request, actor: Principal = Depends(require("user:manage")),
           db: Session = Depends(get_db)):
    user = _get_user(db, user_id)
    user.locked_until = None
    user.failed_login_count = 0
    audit.emit(db, "user.unlocked", request=request, actor=actor, resource_type="user", resource_id=user.id,
               details={"email": user.email})
    db.commit()
    return user


@users.delete("/{user_id}", status_code=204, summary="Delete a user")
def delete_user(user_id: uuid.UUID, request: Request, actor: Principal = Depends(require("user:manage")),
                db: Session = Depends(get_db)):
    """Permanently removes the account and signs it out everywhere. The activity log keeps what the
    user did. You cannot delete yourself or the last active administrator; to keep the account but
    stop sign-in, deactivate it instead."""
    user = _get_user(db, user_id)
    if str(user.id) == actor.id:
        raise HTTPException(400, "You cannot delete your own account")
    if "admin" in user.role_names and user.is_active:
        other_admins = db.scalar(
            select(func.count()).select_from(User)
            .where(User.id != user.id, User.is_active.is_(True), User.roles.any(Role.name == "admin"))
        )
        if not other_admins:
            raise HTTPException(409, "This is the last active administrator and cannot be deleted")
    audit.emit(db, "user.deleted", request=request, actor=actor, resource_type="user", resource_id=user.id,
               details={"email": user.email, "full_name": user.full_name, "roles": user.role_names})
    # Explicit, because SQLite does not enforce the ON DELETE CASCADE on these tables.
    db.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    db.execute(delete(MfaChallenge).where(MfaChallenge.user_id == user.id))
    db.delete(user)
    db.commit()


# -------------------------------------------------------------------- roles


@roles.get("/permissions", response_model=list[PermissionOut], summary="Permission catalogue")
def list_permissions(_: Principal = Depends(require("role:read")), db: Session = Depends(get_db)):
    return db.scalars(select(Permission).order_by(Permission.code)).all()


@roles.get("/roles", response_model=list[RoleOut], summary="List roles")
def list_roles(_: Principal = Depends(require("role:read")), db: Session = Depends(get_db)):
    return [_role_out(r) for r in db.scalars(select(Role).order_by(Role.name))]


@roles.post("/roles", response_model=RoleOut, status_code=201, summary="Create a role")
def create_role(body: RoleCreate, request: Request, actor: Principal = Depends(require("role:manage")),
                db: Session = Depends(get_db)):
    if db.scalar(select(Role).where(Role.name == body.name)):
        raise HTTPException(409, "A role with this name already exists")
    role = Role(name=body.name, description=body.description, permissions=_resolve_permissions(db, body.permissions))
    db.add(role)
    db.flush()
    audit.emit(db, "role.created", request=request, actor=actor, resource_type="role", resource_id=role.id,
               details={"name": role.name, "permissions": sorted(body.permissions)})
    db.commit()
    return _role_out(role)


def _get_mutable_role(db: Session, role_id: uuid.UUID) -> Role:
    role = db.get(Role, role_id)
    if role is None:
        raise HTTPException(404, "Role not found")
    if role.is_system:
        raise HTTPException(400, "System roles cannot be changed")
    return role


@roles.patch("/roles/{role_id}", response_model=RoleOut, summary="Update a role's description or permissions")
def update_role(role_id: uuid.UUID, body: RoleUpdate, request: Request,
                actor: Principal = Depends(require("role:manage")), db: Session = Depends(get_db)):
    """Permission changes apply immediately to everyone holding the role."""
    role = _get_mutable_role(db, role_id)
    changes: dict = {}
    if body.description is not None and body.description != role.description:
        changes["description"] = {"from": role.description, "to": body.description}
        role.description = body.description
    if body.permissions is not None:
        before = [p.code for p in role.permissions]
        if set(before) != set(body.permissions):
            changes["permissions"] = {"from": before, "to": sorted(body.permissions)}
            role.permissions = _resolve_permissions(db, body.permissions)
    if changes:
        audit.emit(db, "role.updated", request=request, actor=actor, resource_type="role", resource_id=role.id,
                   details={"name": role.name, "changes": changes})
        db.commit()
    return _role_out(role)


@roles.delete("/roles/{role_id}", status_code=204, summary="Delete a role")
def delete_role(role_id: uuid.UUID, request: Request, actor: Principal = Depends(require("role:manage")),
                db: Session = Depends(get_db)):
    """Refused while any user still holds the role."""
    role = _get_mutable_role(db, role_id)
    holders = db.scalar(select(func.count()).select_from(user_roles).where(user_roles.c.role_id == role.id))
    if holders:
        raise HTTPException(409, f"Role is assigned to {holders} user(s); unassign it first")
    audit.emit(db, "role.deleted", request=request, actor=actor, resource_type="role", resource_id=role.id,
               details={"name": role.name})
    db.delete(role)
    db.commit()
