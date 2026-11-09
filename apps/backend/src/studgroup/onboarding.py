"""Resumable, server-persisted onboarding after verified Telegram group admission.

The bot owns group invitations; this API never accepts a public group code or joins
a group. Existing memberships were marked complete by migration 0053.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import subgroups
from studgroup.api import ApiError, active_membership, database
from studgroup.models import OnboardingProgress, UserProfile

router = APIRouter(prefix="/v1/onboarding")

# New flow follows the screen order. Legacy adjacent transitions remain permitted
# while older Mini App bundles are still cached by Telegram.
STEPS = ("welcome", "personal", "contacts", "subgroups", "sdo", "legal", "appearance", "finish")
Step = Literal["welcome", "personal", "contacts", "subgroups", "sdo", "legal", "appearance", "finish"]
NEXT_STEPS = {
    "welcome": {"personal", "legal"},
    "personal": {"welcome", "contacts", "subgroups", "legal"},
    "contacts": {"personal", "subgroups"},
    "subgroups": {"contacts", "personal", "sdo"},
    "sdo": {"subgroups", "legal", "finish"},
    "legal": {"sdo", "appearance", "personal", "welcome"},
    "appearance": {"legal", "finish"},
    "finish": {"appearance", "sdo"},
}


async def progress_row(db: AsyncSession, member):
    return await db.get(OnboardingProgress, member.id)


def stage_for(member, row):
    if row and row.completed_at:
        return "complete"
    return row.step if row is not None else ("welcome" if member.role == "student" else "personal")


@router.get("")
async def state(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    group, member = await active_membership(db, authorization)
    row = await progress_row(db, member)
    return {
        "role": member.role,
        "step": stage_for(member, row),
        "completed": bool(row and row.completed_at),
        "legal_required": request.app.state.settings.legal_onboarding_required,
        "group_name": group.name,
        "academic_group_code": group.academic_group_code,
    }


class ProgressBody(BaseModel):
    step: Step


@router.put("/progress")
async def save_progress(
    body: ProgressBody,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    _, member = await active_membership(db, authorization)
    row = await progress_row(db, member)
    current = stage_for(member, row)
    if current == "complete":
        return {"step": "complete", "completed": True}
    if member.role != "student" and body.step == "welcome":
        raise ApiError("invalid_step", "Для старосты доступна настройка профиля", 422)
    if body.step != current and body.step not in NEXT_STEPS[current]:
        # Older staff builds finish their one-page form directly from personal.
        if not (member.role != "student" and body.step == "finish"):
            raise ApiError("invalid_step", "Завершите текущий этап регистрации", 422)
    if row is None:
        row = OnboardingProgress(
            membership_id=member.id, step=body.step, updated_at=datetime.now(UTC)
        )
        db.add(row)
    else:
        row.step = body.step
        row.updated_at = datetime.now(UTC)
    await db.commit()
    return {"step": row.step, "completed": False}


@router.post("/complete")
async def complete(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    _, member = await active_membership(db, authorization)
    row = await progress_row(db, member)
    if row and row.completed_at:
        return {"completed": True, "step": "complete"}
    if stage_for(member, row) != "finish":
        raise ApiError("invalid_step", "Сначала завершите шаги настройки", 422)
    profile = await db.get(UserProfile, member.user_id)
    if not profile or not profile.first_name or not profile.first_name.strip():
        raise ApiError("profile_required", "Укажите имя для профиля", 422)
    if member.role == "student" and await subgroups.unanswered(db, member):
        raise ApiError("subgroups_required", "Выберите подгруппы по всем предметам", 422)
    if request.app.state.settings.legal_onboarding_required:
        from studgroup.legal import has_consent, legal_ready

        settings = request.app.state.settings
        if not legal_ready(settings):
            raise ApiError("legal_not_ready", "Документы оператора ещё не утверждены", 503)
        if not await has_consent(db, member.user_id, "terms", settings):
            raise ApiError("terms_required", "Примите условия использования", 403)
        if not await has_consent(db, member.user_id, "service", settings):
            raise ApiError("consent_required", "Подтвердите обработку данных", 403)
    if row is None:
        row = OnboardingProgress(
            membership_id=member.id, step="complete", updated_at=datetime.now(UTC)
        )
        db.add(row)
    row.step = "complete"
    row.completed_at = datetime.now(UTC)
    row.updated_at = row.completed_at
    await db.commit()
    return {"completed": True, "step": "complete"}
