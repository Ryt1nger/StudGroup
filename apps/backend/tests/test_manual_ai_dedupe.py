"""Headman-created homework cannot be silently duplicated by a later chat package."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_card_edit import HEADERS, set_role
from test_package_worker import PackageProvider, make_new, seed, step

from studgroup.analysis.manual_dedupe import possible_manual_match
from studgroup.models import CanonicalAcademicItem, Homework, ShadowEvaluationRun

pytest_plugins = ["test_schedule_api"]


def record(title, *, description="", subject="Основы российской государственности", date=None):
    return {
        "id": "manual-homework",
        "title": title,
        "description": description,
        "subject": subject,
        "deadline_at": date,
    }


def test_screenshot_paraphrases_and_one_day_deadline_difference_require_review():
    due = datetime(2026, 11, 9, 12, tzinfo=UTC)
    manual = record(
        "Конституционные принципы и основы конституционного строя",
        description="Подготовить домашнее задание по теме конституционных принципов и основ",
        date=due,
    )
    assert possible_manual_match(
        manual,
        subject=manual["subject"],
        title="Разобрать конституционные принципы",
        description="Разобрать конституционные принципы, открыть и изучить по Конституции",
        deadline_at=due + timedelta(days=1),
    )


def test_manual_cards_of_another_subject_or_later_assignment_are_not_blocked():
    manual = record(
        "Конституционные принципы и основы конституционного строя",
        description="Подготовить домашнее задание по конституционным принципам",
        date=datetime(2026, 11, 9, 12, tzinfo=UTC),
    )
    attrs = {
        "title": "Разобрать конституционные принципы",
        "description": "Разобрать конституционные принципы, изучить по Конституции",
    }
    assert not possible_manual_match(
        manual,
        subject="История России",
        deadline_at=manual["deadline_at"],
        **attrs,
    )
    assert not possible_manual_match(
        manual,
        subject=manual["subject"],
        deadline_at=manual["deadline_at"] + timedelta(days=14),
        **attrs,
    )
    assert not possible_manual_match(
        manual,
        subject=manual["subject"],
        title="Презентация о государстве",
        description="Составить слайды и выступить перед группой",
        deadline_at=manual["deadline_at"],
    )


def test_same_subject_distinct_numbered_assignments_remain_publishable():
    manual = record("Решить задачи 1–3", description="Решить задачи письменно.", subject="Математика")
    assert not possible_manual_match(
        manual,
        subject="Математика",
        title="Решить задачи 4–6",
        description="Решить задачи письменно.",
        deadline_at=None,
    )
    assert possible_manual_match(
        manual,
        subject="Математика",
        title="Решить задачи 1–4",
        description="Решить задачи письменно.",
        deadline_at=None,
    )


def test_later_telegram_repeat_is_held_before_publish_without_third_ai_call(client):
    set_role(client, "headman")
    response = client.post(
        "/v1/homework",
        headers=HEADERS,
        json={
            "subject": "Математика",
            "title": "Решить задачи 1–3",
            "description": "По математике решить задачи 1–3.",
        },
    )
    assert response.status_code == 201, response.text
    manual_id = response.json()["id"]
    seed(client)
    make_new(client)
    provider = PackageProvider()
    assert step(client, provider) == "completed"
    assert len(provider.requests) == 2
    payload = provider.requests[1].user_payload
    assert payload["manual_cards"][0]["title"] == "Решить задачи 1–3"
    assert payload["existing_items"] == []

    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            items = (await db.scalars(select(Homework))).all()
            count = await db.scalar(select(func.count()).select_from(CanonicalAcademicItem))
            run = await db.scalar(select(ShadowEvaluationRun))
            return items, count, json.loads(run.metrics_json)

    items, count, metrics = asyncio.run(read())
    assert len(items) == 1 and str(items[0].id) == manual_id
    assert items[0].verification_state == "manual_confirmed"
    assert count == 0
    assert metrics["promotion"]["reasons"] == {"review:possible_manual_duplicate": 1}
