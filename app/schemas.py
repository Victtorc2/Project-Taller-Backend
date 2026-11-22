from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models import AppointmentStatus, Channel, RequestStatus, Role


class FromORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=60, pattern=r"^[a-z0-9._-]+$")
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    role: Role


class UserOut(FromORM):
    id: int
    username: str
    full_name: str
    role: Role
    active: bool


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)


class ClientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    document: str | None = Field(default=None, max_length=30)
    phone: str = Field(min_length=6, max_length=30)
    email: str | None = Field(default=None, max_length=160)


class ClientUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    document: str | None = Field(default=None, max_length=30)
    phone: str | None = Field(default=None, min_length=6, max_length=30)
    email: str | None = Field(default=None, max_length=160)

    @field_validator("full_name", "phone")
    @classmethod
    def required_when_updated(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Este campo no puede ser nulo")
        return value


class ClientOut(FromORM):
    id: int
    full_name: str
    document: str | None
    phone: str
    email: str | None
    active: bool


class VehicleCreate(BaseModel):
    client_id: int
    plate: str = Field(min_length=5, max_length=15)
    brand: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    year: int | None = Field(default=None, ge=1900, le=2100)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("plate")
    @classmethod
    def normalize_plate(cls, value: str) -> str:
        return value.strip().upper().replace(" ", "")


class VehicleUpdate(BaseModel):
    client_id: int | None = None
    plate: str | None = Field(default=None, min_length=5, max_length=15)
    brand: str | None = Field(default=None, max_length=60)
    model: str | None = Field(default=None, max_length=60)
    year: int | None = Field(default=None, ge=1900, le=2100)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("client_id")
    @classmethod
    def client_required_when_updated(cls, value: int | None) -> int:
        if value is None:
            raise ValueError("El cliente no puede ser nulo")
        return value

    @field_validator("plate")
    @classmethod
    def normalize_plate(cls, value: str | None) -> str | None:
        return value.strip().upper().replace(" ", "") if value else value


class VehicleOut(FromORM):
    id: int
    client_id: int
    plate: str
    brand: str | None
    model: str | None
    year: int | None
    notes: str | None
    active: bool


class RequestCreate(BaseModel):
    client_id: int
    vehicle_id: int
    channel: Channel
    reason: str = Field(min_length=5, max_length=3000)


class RequestOut(FromORM):
    id: int
    client_id: int
    vehicle_id: int
    channel: Channel
    reason: str
    status: RequestStatus
    created_by: int
    created_at: datetime

    @field_serializer("created_at")
    def utc_created(self, value: datetime) -> str:
        return value.replace(tzinfo=timezone.utc).isoformat()


class AppointmentCreate(BaseModel):
    request_id: int
    starts_at: datetime


class AppointmentReschedule(BaseModel):
    starts_at: datetime


class AppointmentOut(FromORM):
    id: int
    request_id: int
    starts_at: datetime
    status: AppointmentStatus
    confirmed_at: datetime | None
    created_by: int
    created_at: datetime

    @field_serializer("starts_at", "confirmed_at", "created_at")
    def utc_date(self, value: datetime | None) -> str | None:
        return value.replace(tzinfo=timezone.utc).isoformat() if value else None


class AvailabilityOut(BaseModel):
    starts_at: datetime
    available: int
