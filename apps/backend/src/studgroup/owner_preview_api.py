"""Private, owner-only browser view of stored shadow-wave results.

This API intentionally never runs AI or writes student-facing entities.  Review
notes are stored separately so that feedback cannot accidentally publish a card.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, active_membership, database
from studgroup.models import OwnerPreviewReview, User

router = APIRouter(prefix="/v1/owner")
_WAVE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


async def owner_context(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    group, member = await active_membership(db, authorization)
    user = await db.get(User, member.user_id)
    owner_id = request.app.state.settings.owner_telegram_user_id
    if owner_id is None or user is None or user.telegram_user_id != owner_id:
        raise ApiError("permission_denied", "Доступен только владельцу", 403)
    return group, user


async def bundle(db, wave_key, group_id):
    # Delayed import avoids the CLI exporter -> Settings -> app construction cycle.
    from studgroup.export_shadow_wave import ShadowWaveUnavailable, export_wave

    if not _WAVE.fullmatch(wave_key):
        raise ApiError("not_found", "Результат preview не найден", 404)
    try:
        return await export_wave(
            db, wave_key, expected_group_id=group_id, max_runs=200, max_stored_bytes=5_000_000
        )
    except ShadowWaveUnavailable as error:
        raise ApiError("not_found", "Сохранённых shadow-результатов для этой волны нет", 404) from error


@router.get("/ai-preview/{wave_key}")
async def get_preview(
    wave_key: str,
    context: Annotated[tuple, Depends(owner_context)],
    db: Annotated[AsyncSession, Depends(database)],
):
    group, _user = context
    result = await bundle(db, wave_key, group.id)
    cards = result["preview"]["cards"]
    notes = await db.scalars(
        select(OwnerPreviewReview).where(
            OwnerPreviewReview.group_id == group.id, OwnerPreviewReview.wave_key == wave_key
        ).order_by(OwnerPreviewReview.created_at.desc())
    )
    return {
        "wave": wave_key,
        "counts": result["counts"],
        "cards": cards,
        "review_marks": result["preview"]["review_marks"],
        "outcomes": result["preview"]["application_outcomes"],
        "reviews": [review_view(note) for note in notes],
        "read_only": True,
    }


class ReviewBody(BaseModel):
    card_id: str = Field(pattern=r"^shadow-preview-[1-9][0-9]*$")
    kind: Literal[
        "correct", "wrong_subject", "wrong_deadline", "incomplete_description", "extra_material",
        "missing_material", "false_homework", "wrong_merge", "other",
    ]
    comment: str = Field(min_length=1, max_length=2000)


def review_view(note):
    return {
        "id": note.id, "card_id": note.card_id, "kind": note.kind,
        "comment": note.comment, "created_at": note.created_at,
    }


@router.post("/ai-preview/{wave_key}/reviews", status_code=201)
async def add_review(
    wave_key: str,
    body: ReviewBody,
    context: Annotated[tuple, Depends(owner_context)],
    db: Annotated[AsyncSession, Depends(database)],
):
    group, user = context
    result = await bundle(db, wave_key, group.id)
    if body.card_id not in {card["id"] for card in result["preview"]["cards"]}:
        raise ApiError("invalid_request", "Карточка не принадлежит этой волне", 422)
    note = OwnerPreviewReview(
        id=uuid4(), group_id=group.id, author_id=user.id, wave_key=wave_key,
        card_id=body.card_id, kind=body.kind, comment=body.comment.strip(), created_at=datetime.now(UTC),
    )
    db.add(note)
    await db.commit()
    return review_view(note)
