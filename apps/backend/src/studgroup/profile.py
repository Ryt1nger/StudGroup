"""The caller's own profile: name, contact, study platform and notification preferences.

Everything here is personal: a user reads and writes only their own row. The study-platform password
is write-only: it is encrypted before it is stored and no endpoint ever returns it, only whether one
is set.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import User, UserProfile, WebSession
from studgroup.ranepa_credential_store import CredentialStoreUnavailable, configured_store
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
            "password_set": bool(row.sdu_credential_id or row.sdu_password_encrypted),
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
    request: Request,
    db: Annotated[AsyncSession, Depends(database)], authorization: str | None = Header(None)
):
    user, row = await current(db, authorization)
    result = representation(user, row)
    if row and row.sdu_credential_id:
        store = configured_store(request.app.state.settings)
        if store is None:
            raise ApiError("service_unavailable", "Хранение реквизитов недоступно", 503)
        try:
            credential = await store.get(row.sdu_credential_id)
        except CredentialStoreUnavailable:
            raise ApiError("service_unavailable", "Хранение реквизитов недоступно", 503) from None
        if credential:
            result["sdu"]["login"] = credential[0]
    return result


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
    # Credentials are voluntary and require a separately recorded, current consent.
    supplied_sdo = any(
        name in fields and getattr(body, name) is not None
        for name in ("sdu_platform", "sdu_url", "sdu_login", "sdu_calendar_url", "sdu_password")
    )
    if supplied_sdo:
        from studgroup.api import active_membership
        from studgroup.legal import has_consent, legal_ready

        if not legal_ready(request.app.state.settings):
            raise ApiError("legal_not_ready", "Обработка учётных данных пока недоступна", 503)
        await active_membership(db, authorization)
        if not await has_consent(db, user.id, "sdo", request.app.state.settings):
            raise ApiError("consent_required", "Сначала подтвердите отдельное согласие СДО/ЛК", 403)
    if row is None:
        row = UserProfile(user_id=user.id, updated_at=datetime.now(UTC))
        db.add(row)
    store = configured_store(request.app.state.settings)
    ydb_record = None
    if store and row.sdu_credential_id:
        try:
            ydb_record = await store.get(row.sdu_credential_id)
        except CredentialStoreUnavailable:
            raise ApiError("service_unavailable", "Хранение реквизитов недоступно", 503) from None
    for name in (
        "first_name",
        "last_name",
        "email",
        "sdu_platform",
        "sdu_url",
        "sdu_calendar_url",
    ):
        if name in fields:
            setattr(row, name, getattr(body, name))
    if "settings" in fields and body.settings is not None:
        row.settings = body.settings.model_dump()
    if store and ("sdu_login" in fields or body.sdu_password is not None or body.clear_sdu_password):
        if body.clear_sdu_password:
            if row.sdu_credential_id:
                try:
                    await store.delete(row.sdu_credential_id)
                except CredentialStoreUnavailable:
                    raise ApiError("service_unavailable", "Удаление реквизитов не подтверждено", 503) from None
            row.sdu_credential_id = None
            row.sdu_login = None
            row.sdu_password_encrypted = None
        else:
            login = body.sdu_login if "sdu_login" in fields else (ydb_record[0] if ydb_record else row.sdu_login)
            if body.sdu_password is not None:
                secret = body.sdu_password.get_secret_value()
                if not secret:
                    raise ApiError("invalid_request", "Пароль не может быть пустым", 422)
                fernet = cipher(request)
                if fernet is None:
                    raise ApiError("service_unavailable", "Хранение пароля сейчас недоступно", 503)
                ciphertext = fernet.encrypt(secret.encode()).decode()
            else:
                ciphertext = ydb_record[1] if ydb_record else row.sdu_password_encrypted
            if login and ciphertext:
                record_id = row.sdu_credential_id or uuid.uuid4()
                try:
                    await store.put(record_id, login, ciphertext, "v1")
                except CredentialStoreUnavailable:
                    raise ApiError("service_unavailable", "Сохранение реквизитов не подтверждено", 503) from None
                row.sdu_credential_id = record_id
                row.sdu_login = None
                row.sdu_password_encrypted = None
                ydb_record = (login, ciphertext, "v1")
            elif body.sdu_password is not None or "sdu_login" in fields:
                raise ApiError("invalid_request", "Введите логин вместе с паролем", 422)
    elif not store:
        if supplied_sdo and not request.app.state.settings.ranepa_legacy_credentials_allowed:
            raise ApiError("service_unavailable", "Хранение реквизитов недоступно", 503)
        if "sdu_login" in fields:
            row.sdu_login = body.sdu_login
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
    result = representation(user, row)
    if ydb_record and row.sdu_credential_id:
        result["sdu"]["login"] = ydb_record[0]
    return result
