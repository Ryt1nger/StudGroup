"""Owner-funded pilot: all approved groups get AI without per-group paid activation."""

import asyncio
import io
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_staff_api import AUTH, make

from studgroup.ai import ProviderFailure
from studgroup.analysis.image_jobs import enabled, live_mode
from studgroup.analysis.package_worker import _gate
from studgroup.analysis.pilot_access import enroll_existing, ensure_route
from studgroup.analysis.release_budget import RequestAccounting, release_window_blocker
from studgroup.models import AnalysisBudgetCall, Group, GroupAnalysisSettings

pytest_plugins = ["test_schedule_api"]


def pilot(client):
    settings = client.app.state.settings
    settings.owner_funded_pilot = True
    assert asyncio.run(enroll_existing(client.app.state.engine)) == 1
    return settings


def test_existing_approved_group_gets_cplus_route_without_spending_grant(client):
    settings = pilot(client)

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            approved = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            route = await db.get(GroupAnalysisSettings, approved.id)
            assert route.analysis_mode == "new"
            assert route.release_budget_usd is None
            assert route.shadow_daily_budget_usd == Decimal(0)
            assert await ensure_route(db, other) is None
            assert await db.get(GroupAnalysisSettings, other.id) is None
            assert (
                release_window_blocker(
                    route, datetime.now(UTC), settings=settings, group_id=approved.id
                )
                is None
            )
            assert enabled(settings, approved.id)
            assert live_mode(settings)
            assert _gate(settings, datetime.now(UTC))

    asyncio.run(check())
    assert asyncio.run(enroll_existing(client.app.state.engine)) == 0


def test_owner_funded_ai_still_reserves_and_settles_every_request(client):
    settings = pilot(client)
    calls = []

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group_id = group.id

        async def fake_deepseek():
            calls.append("called")
            return {
                "usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "prompt_cache_hit_tokens": 0,
                }
            }

        accounting = RequestAccounting(
            client.app.state.engine,
            settings,
            group_id,
            kind="cplus",
            attempt_key="pilot-test-1",
        )
        await accounting.execute(
            {
                "model": "deepseek-flash",
                "messages": [{"role": "user", "content": "Student message"}],
                "max_tokens": 1000,
            },
            fake_deepseek,
        )
        async with AsyncSession(client.app.state.engine) as db:
            recorded = (await db.scalars(select(AnalysisBudgetCall))).all()
            assert len(recorded) == 1
            assert recorded[0].group_id == group_id
            assert recorded[0].state == "settled"
            assert recorded[0].charged_usd > 0
            assert recorded[0].reserved_usd == 0
            assert (await db.get(GroupAnalysisSettings, group_id)).release_budget_usd is None

    asyncio.run(run())
    assert calls == ["called"]


def test_unapproved_group_cannot_spend_under_pilot(client):
    settings = pilot(client)

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            ident = other.id

        async def never_called():
            raise AssertionError("must not invoke external provider")

        account = RequestAccounting(
            client.app.state.engine,
            settings,
            ident,
            kind="cplus",
            attempt_key="unauthorized",
        )
        with pytest.raises(ProviderFailure, match="production_group_not_configured"):
            await account.execute(
                {
                    "model": "deepseek-flash",
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 200,
                },
                never_called,
            )

    asyncio.run(run())


def test_staff_can_upload_schedule_photo_without_group_budget(client):
    make(client, "headman")
    pilot(client)
    image = io.BytesIO()
    Image.new("RGB", (220, 160)).save(image, format="PNG")
    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.weekday())
    response = client.post(
        "/v1/staff/schedule-upload",
        headers=AUTH,
        files={"image": ("week.png", image.getvalue(), "image/png")},
        data={"week_start": monday.isoformat(), "duration": "one_week"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["state"] == "queued"
    assert client.get("/v1/staff/image-findings/schedule", headers=AUTH).status_code == 200
