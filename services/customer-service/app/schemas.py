import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator

EmploymentType = Literal["full_time", "part_time", "self_employed", "contract", "unemployed", "retired", "other"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------- customers


class CustomerCreate(_Strict):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)
    date_of_birth: date | None = None
    address_line1: str | None = Field(default=None, max_length=200)
    address_line2: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    postal_code: str | None = Field(default=None, max_length=20)
    country: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)


class CustomerUpdate(CustomerCreate):
    """Partial update: send only the fields to change."""

    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    email: EmailStr | None = None

    @model_validator(mode="after")
    def _required_fields_not_null(self):
        for name in ("first_name", "last_name", "email"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    first_name: str
    last_name: str
    email: str
    phone: str | None
    date_of_birth: date | None
    address_line1: str | None
    address_line2: str | None
    city: str | None
    state: str | None
    postal_code: str | None
    country: str | None
    notes: str | None
    status: Literal["active", "blocked"]
    block_reason: str | None
    blocked_at: datetime | None
    blocked_by: str | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


class CustomerPage(BaseModel):
    items: list[CustomerOut]
    total: int
    page: int
    page_size: int


class BlockIn(_Strict):
    reason: str = Field(min_length=3, max_length=500)


# --------------------------------------------------------------- employment


class EmploymentCreate(_Strict):
    employer_name: str = Field(min_length=1, max_length=200)
    job_title: str | None = Field(default=None, max_length=200)
    employment_type: EmploymentType = "full_time"
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = True
    annual_income: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _dates_in_order(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date cannot be before start_date")
        return self


class EmploymentUpdate(EmploymentCreate):
    employer_name: str | None = Field(default=None, min_length=1, max_length=200)
    employment_type: EmploymentType | None = None
    is_current: bool | None = None


class EmploymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    employer_name: str
    job_title: str | None
    employment_type: str
    start_date: date | None
    end_date: date | None
    is_current: bool
    annual_income: Decimal | None
    currency: str | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime


# ----------------------------------------------------------------- contacts


class ContactCreate(_Strict):
    full_name: str = Field(min_length=1, max_length=200)
    relationship_type: str = Field(min_length=1, max_length=64, examples=["spouse"])
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    is_emergency_contact: bool = False
    notes: str | None = Field(default=None, max_length=5000)


class ContactUpdate(ContactCreate):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    relationship_type: str | None = Field(default=None, min_length=1, max_length=64)
    is_emergency_contact: bool | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_id: uuid.UUID
    full_name: str
    relationship_type: str
    email: str | None
    phone: str | None
    is_emergency_contact: bool
    notes: str | None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
