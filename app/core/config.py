from functools import lru_cache
from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(min_length=1)
    jwt_secret: str = ""
    access_token_minutes: int = Field(default=60, ge=5, le=1440)
    business_timezone: str = "America/Lima"
    appointment_capacity: int = Field(default=1, ge=1, le=20)
    appointment_minutes: int = Field(default=60, ge=15, le=240)
    open_hour: int = Field(default=8, ge=0, le=23)
    close_hour: int = Field(default=18, ge=1, le=23)
    cors_origins: str = "http://localhost:5173"

    @field_validator("jwt_secret")
    @classmethod
    def secret_length(cls, value: str) -> str:
        if len(value) < 32 or value == "replace-with-a-long-random-secret-at-least-32-characters":
            raise ValueError("JWT_SECRET debe ser un secreto aleatorio de al menos 32 caracteres")
        return value

    @field_validator("business_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            if value != "America/Lima":
                raise ValueError("Instale tzdata para usar esta zona horaria") from None
        return value


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if settings.open_hour >= settings.close_hour:
        raise ValueError("OPEN_HOUR debe ser menor que CLOSE_HOUR")
    return settings


def get_business_tz() -> tzinfo:
    name = get_settings().business_timezone
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name == "America/Lima":
            return timezone(timedelta(hours=-5), name)
        raise
