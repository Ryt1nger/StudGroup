"""Authenticated student's subgroup choices for the Mini App profile."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import subgroups
from studgroup.api import ApiError, active_group, database
from studgroup.homework import user_id
from studgroup.models import Group

router = APIRouter(prefix="/v1")


class Choice(BaseModel):
    subgroup_id: uuid.UUID


async def response(db, group, member):
    picked = await subgroups.choices(db, member.id)
    return {
        "items": [
            {
                "split_id": split.id,
                "subject": {"id": split.subject_id, "name": split.subject_name},
                "options": [{"id": row.id, "name": row.name} for row in rows],
                "selected_subgroup_id": picked.get(split.id),
            }
            for split, rows in await subgroups.splits(db, group.id)
        ]
    }


@router.get("/subgroups")
async def mine(
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str = Header(),
):
    member = await subgroups.membership(db, group.id, await user_id(db, authorization))
    if member is None:
        raise ApiError("permission_denied", "Нет активного доступа к группе", 403)
    return await response(db, group, member)


@router.put("/subgroups/{split_id}")
async def choose(
    split_id: uuid.UUID,
    body: Choice,
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str = Header(),
):
    member = await subgroups.membership(db, group.id, await user_id(db, authorization))
    if member is None:
        raise ApiError("permission_denied", "Нет активного доступа к группе", 403)
    try:
        await subgroups.choose(db, member, split_id, body.subgroup_id)
    except ValueError as error:
        code = str(error)
        if code == "unknown_split":
            raise ApiError("not_found", "Предмет с подгруппами не найден", 404) from None
        raise ApiError("invalid_request", "Подгруппа не относится к этому предмету", 422) from None
    await db.commit()
    return await response(db, group, member)
