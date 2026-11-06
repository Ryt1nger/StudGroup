"""Self-service, write-only RANEPA connection for future read-only collection."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import RanepaAccount
from studgroup.profile import cipher, current

router = APIRouter(prefix="/v1/ranepa-account")


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: SecretStr = Field(min_length=1, max_length=120)
    password: SecretStr = Field(min_length=1, max_length=200)


@router.get("")
async def connection_state(
    db: Annotated[AsyncSession, Depends(database)], authorization: str | None = Header(None)
):
    user, _ = await current(db, authorization)
    row = await db.get(RanepaAccount, user.id)
    return {"connected": row is not None, "updated_at": row.updated_at if row else None}


@router.put("")
async def set_connection(
    body: AccountInput,
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    user, _ = await current(db, authorization)
    login = body.login.get_secret_value().strip()
    password = body.password.get_secret_value()
    if not login or not password.strip():
        raise ApiError("invalid_request", "Укажите логин и пароль", 422)
    fernet = cipher(request)
    if fernet is None:
        raise ApiError("service_unavailable", "Защищённое хранение сейчас недоступно", 503)
    row = await db.get(RanepaAccount, user.id)
    if row is None:
        row = RanepaAccount(user_id=user.id)
        db.add(row)
    row.login_encrypted = fernet.encrypt(login.encode()).decode()
    row.password_encrypted = fernet.encrypt(password.encode()).decode()
    row.updated_at = datetime.now(UTC)
    await db.commit()
    return {"connected": True, "updated_at": row.updated_at}


@router.delete("")
async def clear_connection(
    db: Annotated[AsyncSession, Depends(database)], authorization: str | None = Header(None)
):
    user, _ = await current(db, authorization)
    row = await db.get(RanepaAccount, user.id)
    if row is not None:
        await db.delete(row)
        await db.commit()
    return Response(status_code=204)
