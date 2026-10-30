"""The caller's own profile: name, contact, study platform and notification preferences.

Everything here is personal: a user reads and writes only their own row. The study-platform password
is write-only: it is encrypted before it is stored and no endpoint ever returns it, only whether one
is set.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import User, UserProfile, WebSession
from studgroup.security import token_hash

router = APIRouter(prefix="/v1")


class NotificationSettings(BaseModel):
    """Preferences the bot honours when it messages the person (all default to the calm choice)."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    new_cards: bool = True
    changes: bool = True
    deadlines: bool = True
    deadline_lead: Literal["3h", "1d", "2d"] = "1d"
    lessons: bool = False
    lesson_lead_minutes: Literal[10, 15, 30] = 15
    quiet: bool = True
    quiet_range: Literal["23-08", "22-09", "00-07"] = "23-08"


def text(value, limit):
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise ValueError("too_long")
    return cleaned or None


def https_url(value, limit):
    cleaned = text(value, limit)
    if cleaned is None:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("https_required")
    return cleaned


class UpdateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    sdu_platform: str | None = None
    sdu_url: str | None = None
    sdu_login: str | None = None
    sdu_calendar_url: str | None = None
    # Write-only. An empty string is not "clear": use ``clear_sdu_password``.
    sdu_password: SecretStr | None = Field(default=None, max_length=200)
    clear_sdu_password: bool = False
    settings: NotificationSettings | None = None

    @field_validator("first_name", "last_name", mode="before")
    @classmethod
    def names(cls, value):
        return text(value, 60)

    @field_validator("sdu_platform", "sdu_login", mode="before")
    @classmethod
    def short(cls, value):
        return text(value, 80)

    @field_validator("email", mode="before")
    @classmethod
    def mail(cls, value):
        cleaned = text(value, 120)
        if cleaned is not None and not ("@" in cleaned and "." in cleaned.split("@")[-1]):
            raise ValueError("invalid_email")
        return cleaned

    @field_validator("sdu_url", mode="before")
    @classmethod
    def platform_url(cls, value):
        return https_url(value, 200)

    @field_validator("sdu_calendar_url", mode="before")
    @classmethod
    def calendar_url(cls, value):
        return https_url(value, 300)


def cipher(request: Request) -> Fernet | None:
    key = request.app.state.settings.profile_encryption_key.get_secret_value()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except ValueError:
        return None


def representation(user: User, row: UserProfile | None):
    row = row or UserProfile(user_id=user.id)
    settings = NotificationSettings.model_validate(row.settings or {})
    return {
        "telegram_user_id": user.telegram_user_id,
        "username": user.username,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "email": row.email,
        "sdu": {
            "platform": row.sdu_platform,
            "url": row.sdu_url,
            "login": row.sdu_login,
            "calendar_url": row.sdu_calendar_url,
            "password_set": bool(row.sdu_password_encrypted),
        },
        "settings": settings.model_dump(),
    }


async def current(db, authorization):
    """The caller and their profile row; an absent, unknown or expired session is a 401 like elsewhere."""
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError("unauthenticated", "Откройте приложение из Telegram")
    credential = await db.get(WebSession, token_hash(authorization[7:]))
    expiry = credential.expires_at if credential else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if credential is None or expiry <= datetime.now(UTC):
        raise ApiError("session_expired", "Откройте приложение заново из Telegram")
    uid = credential.user_id
    return await db.get(User, uid), await db.get(UserProfile, uid)


@router.get("/profile")
async def read_profile(
    db: Annotated[AsyncSession, Depends(database)], authorization: str | None = Header(None)
):
    user, row = await current(db, authorization)
    return representation(user, row)


@router.patch("/profile")
async def update_profile(
    body: UpdateProfile,
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    user, row = await current(db, authorization)
    fields = body.model_fields_set
    if not fields:
        raise ApiError("invalid_request", "Нет изменений", 422)
    if row is None:
        row = UserProfile(user_id=user.id, updated_at=datetime.now(UTC))
        db.add(row)
    for name in (
        "first_name",
        "last_name",
        "email",
        "sdu_platform",
        "sdu_url",
        "sdu_login",
        "sdu_calendar_url",
    ):
        if name in fields:
            setattr(row, name, getattr(body, name))
    if "settings" in fields and body.settings is not None:
        row.settings = body.settings.model_dump()
    if body.clear_sdu_password:
        row.sdu_password_encrypted = None
    elif body.sdu_password is not None:
        secret = body.sdu_password.get_secret_value()
        if not secret:
            raise ApiError("invalid_request", "Пароль не может быть пустым", 422)
        fernet = cipher(request)
        if fernet is None:
            raise ApiError("service_unavailable", "Хранение пароля сейчас недоступно", 503)
        row.sdu_password_encrypted = fernet.encrypt(secret.encode()).decode()
    row.updated_at = datetime.now(UTC)
    await db.commit()
    return representation(user, row)
