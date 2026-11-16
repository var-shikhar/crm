import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from crm_common import validation

from .config import settings

Password = Field(min_length=settings.password_min_length, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    refresh_token: str | None = None
    scope: str = ""


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    mfa_required: bool
    mfa_method: str
    roles: list[str] = Field(validation_alias="role_names")
    is_locked: bool
    last_login_at: datetime | None
    created_at: datetime


class MeOut(UserOut):
    permissions: list[str]


FullName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
                     AfterValidator(validation.person_name)]


class UserCreate(BaseModel):
    email: EmailStr
    full_name: FullName
    password: str = Password
    roles: list[str] = []
    mfa_required: bool = Field(default=True, description="If false, the account starts with no second factor")


class UserUpdate(BaseModel):
    full_name: FullName | None = None
    is_active: bool | None = None
    roles: list[str] | None = None
    mfa_required: bool | None = Field(default=None, description="Require a second factor at sign-in")


class SetPassword(BaseModel):
    new_password: str = Password


class ChangePassword(BaseModel):
    current_password: str
    new_password: str = Password


class PasswordConfirm(BaseModel):
    password: str


class OtpIn(BaseModel):
    otp: str = Field(min_length=6, max_length=10)


class TotpSetupOut(BaseModel):
    secret: str
    otpauth_uri: str
    qr_svg: str


class RoleOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    is_system: bool
    permissions: list[str]


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(default="", max_length=255)
    permissions: list[str] = []


class RoleUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=255)
    permissions: list[str] | None = None


class PermissionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    description: str


class AuthzCheckIn(BaseModel):
    token: str
    permission: str
    context: dict = {}


class PrincipalOut(BaseModel):
    id: str
    type: str
    email: str | None = None
    name: str | None = None
    roles: list[str] = []
    permissions: list[str] = []


class AuthzCheckOut(BaseModel):
    active: bool
    allowed: bool
    principal: PrincipalOut | None = None
