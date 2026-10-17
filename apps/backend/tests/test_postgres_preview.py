"""Opt-in PostgreSQL gate: owner preview shows real cards, merged supplements and materials."""

import asyncio
import os
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_postgres_backfill import ScriptedProvider, card

from studgroup.analysis import backfill
from studgroup.analysis.preview import build_preview
from studgroup.analysis.review_text import render_preview
from studgroup.analysis.routing import configure_group_mode
from studgroup.analysis.shadow_worker import process_shadow_next
from studgroup.main import Settings
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    CanonicalProjectionLink,
    Group,
    Homework,
    Membership,
    MessageAttachment,
    Notification,
    RawMessage,
    User,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)
MSK = ZoneInfo("Europe/Moscow")
HEADMAN_TELEGRAM_ID = 777001
STUDENT_TELEGRAM_ID = 777002


def test_postgres_preview_merges_supplements_and_materials_without_writing():
    url = os.environ["POSTGRES_TEST_URL"]
    now = datetime.now(UTC).replace(microsecond=0)
    from studgroup.deadlines import contextual_deadline

    exam_sent = now - timedelta(hours=8) + timedelta(minutes=330)
    resolved = contextual_deadline("Контрольная в среду", exam_sent, "Europe/Moscow")
    wednesday = resolved.at.astimezone(MSK)

    # id, minutes after the first message, text, sender, reply_to, attachments
    rows = [
        (
            9201,
            0,
            (
                "#орг Каждый делает доклад-презентацию о выдающихся героях (персоналиях) РФ. "
                "Например, учёные, выдающиеся исторические личности и тд."
            ),
            HEADMAN_TELEGRAM_ID,
            None,
            [],
        ),
        (
            9202,
            2,
            "1) Исторические деятели 2) Творцы 3) Учёные 4) Защитники Отечества",
            HEADMAN_TELEGRAM_ID,
            9201,
            [],
        ),
        (
            9203,
            4,
            "",
            HEADMAN_TELEGRAM_ID,
            9201,
            [("document", "Требования к презентации.pdf", "application/pdf", None)],
        ),
        (
            9204,
            6,
            "https://drive.google.com/file/d/abc/view образец оформления",
            HEADMAN_TELEGRAM_ID,
            9201,
            [],
        ),
        (9205, 9, "", STUDENT_TELEGRAM_ID, None, [("photo", None, "image/jpeg", "album-1")]),
        (
            9206,
            300,
            "#орг к следующей паре определиться с командами на проект и темой",
            HEADMAN_TELEGRAM_ID,
            None,
            [],
        ),
        (9207, 330, "Контрольная в среду", HEADMAN_TELEGRAM_ID, None, []),
        (9208, 340, "Перенесли на пятницу", HEADMAN_TELEGRAM_ID, 9207, []),
        (9209, 360, "Иванов, принеси справку в деканат", HEADMAN_TELEGRAM_ID, None, []),
        (
            9210,
            380,
            "В четверг после пар встреча студентов с выпускниками кафедры",
            HEADMAN_TELEGRAM_ID,
            None,
            [],
        ),
    ]
    script = defaultdict(list)
    script[9201] = [card(None, "#орг доклад презентация", "#орг доклад-презентация", [9201])]
    script[9202] = [
        card(
            None,
            "Доклад-презентация о выдающихся героях РФ",
            "Каждый делает доклад-презентацию о выдающихся героях (персоналиях) РФ.\n"
            "Темы: 1) Исторические деятели 2) Творцы 3) Учёные 4) Защитники "
            "Отечества",
            [9201, 9202],
        )
    ]
    script[9206] = [
        card(
            None,
            "Учебное задание по предмету Культура",
            "#орг к следующей паре определиться с командами на проект и темой",
            [9206],
        )
    ]
    script[9207] = [
        card(
            None,
            "Контрольная",
            "Контрольная в среду.",
            [9207],
            at=wednesday.isoformat(),
            kind="test",
        )
    ]
    script[9208] = [
        card(
            None,
            "Контрольная перенесена",
            "Перенесли на пятницу.",
            [9207, 9208],
            at=(wednesday + timedelta(days=2)).isoformat(),
            kind="test",
        )
        | {"action": "reschedule"}
    ]
    script[9209] = [
        card(None, "Принести справку", "Принести справку в деканат.", [9209], audience="personal")
    ]
    # An event is not one of the published categories: a held candidate, never a card.
    script[9210] = [
        {
            "category": "event",
            "title": "Встреча с выпускниками",
            "description": "Встреча студентов с выпускниками кафедры.",
            "when_text": "в четверг после пар",
            "confidence": 80,
            "evidence": [
                {"telegram_message_id": 9210, "revision": 1, "fact_paths": ["description"]}
            ],
        }
    ]

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        settings = Settings(database_url=url, ai_enabled=False, ai_shadow_enabled=True)
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                await db.execute(
                    delete(User).where(
                        User.telegram_user_id.in_([HEADMAN_TELEGRAM_ID, STUDENT_TELEGRAM_ID])
                    )
                )
                db.add(
                    Group(
                        id=group_id,
                        telegram_chat_id=-1004400000000 - int(uuid.uuid4().int % 10**6),
                        name="Disposable preview E2E",
                        timezone="Europe/Moscow",
                        pilot_authorized=True,
                        bot_added_at=now - timedelta(days=2),
                    )
                )
                headman = User(telegram_user_id=HEADMAN_TELEGRAM_ID, display_name="Староста")
                student = User(telegram_user_id=STUDENT_TELEGRAM_ID, display_name="Студент")
                db.add_all([headman, student])
                await db.flush()
                db.add_all(
                    [
                        Membership(
                            user_id=headman.id, group_id=group_id, role="headman", status="active"
                        ),
                        Membership(
                            user_id=student.id, group_id=group_id, role="student", status="active"
                        ),
                    ]
                )
                await db.flush()
                await configure_group_mode(
                    db,
                    group_id,
                    "shadow",
                    now - timedelta(days=1),
                    shadow_daily_budget_usd=Decimal("0.50"),
                    shadow_sample_percent=100,
                )
                start = now - timedelta(hours=8)
                for mid, minutes, text, sender, reply, attachments in rows:
                    stamp = start + timedelta(minutes=minutes)
                    raw = RawMessage(
                        group_id=group_id,
                        telegram_message_id=mid,
                        text=text,
                        sender_id=sender,
                        reply_to_message_id=reply,
                        message_thread_id=591,
                        message_date=stamp,
                        version_date=stamp,
                        revision=1,
                        imported=False,
                        live_received_at=stamp,
                        delete_at=now + timedelta(days=30),
                    )
                    db.add(raw)
                    await db.flush()
                    for index, (kind, name, mime, album) in enumerate(attachments):
                        db.add(
                            MessageAttachment(
                                raw_message_id=raw.id,
                                group_id=group_id,
                                source_revision=1,
                                kind=kind,
                                stable_file_id=f"{mid}-{index}",
                                telegram_file_id=f"tg-{mid}-{index}",
                                file_name=name,
                                mime_type=mime,
                                media_group_id=album,
                                created_at=stamp,
                                delete_at=now + timedelta(days=30),
                            )
                        )
                await db.commit()
            common = {"group_id": group_id, "since": now - timedelta(days=1), "now": now}

            async def counts():
                async with AsyncSession(engine) as db:
                    result = {}
                    for model in (
                        Homework,
                        AcademicDeadline,
                        Notification,
                        CanonicalAcademicItem,
                        CanonicalProjectionLink,
                    ):
                        query = select(func.count()).select_from(model)
                        if hasattr(model, "group_id"):
                            query = query.where(model.group_id == group_id)
                        result[model.__tablename__] = await db.scalar(query)
                    result["revisions"] = await db.scalar(
                        select(func.count()).select_from(CanonicalItemRevision)
                    )
                    result["evidence"] = await db.scalar(
                        select(func.count()).select_from(CanonicalItemEvidence)
                    )
                    result["outbox"] = await db.scalar(select(func.count()).select_from(BotOutbox))
                    return result

            zero = await counts()
            async with AsyncSession(engine) as db:
                await backfill.enqueue_window(
                    db,
                    **common,
                    limit=20,
                    apply=True,
                    marker={"preparation_key": "w-t", "wave": "w-t"},
                )
                await db.commit()
            provider = ScriptedProvider(script)
            statuses = [
                await process_shadow_next(engine, settings, provider, now) for _ in range(12)
            ]
            assert "failed" not in statuses and statuses.count("completed") >= 7
            assert await counts() == zero  # shadow alone writes nothing

            async with AsyncSession(engine) as db:
                preview = await build_preview(db, **common)
                await db.rollback()
            assert await counts() == zero  # preview leaves no trace at all
            async with AsyncSession(engine) as db:
                again = await build_preview(db, **common)
                await db.rollback()
            assert [c["title"] for c in again["cards"]] == [c["title"] for c in preview["cards"]]
            assert await counts() == zero  # idempotent

            cards = preview["cards"]
            assert all("#" not in c["title"] and "#" not in c["description"] for c in cards)
            assert not any(c["title"].startswith("Учебное задание") for c in cards)

            # one card for the report, merged with the enumeration, headman priority visible
            report = next(
                c for c in cards if 9201 in {s["telegram_message_id"] for s in c["sources"]}
            )
            assert {s["telegram_message_id"] for s in report["sources"]} >= {9201, 9202}
            assert report["audience"]["scope"] == "group"
            assert report["subject"] is None and report["when"] == "срок не указан"
            assert (
                sum(1 for c in cards if 9202 in {s["telegram_message_id"] for s in c["sources"]})
                == 1
            )
            assert {s["author_role"] for s in report["sources"]} == {"headman"}
            assert report["status"] == "будет показана"
            kinds = {(m["group"], m["title"]) for m in report["materials"]}
            assert ("file", "Требования к презентации.pdf") in kinds
            assert any(
                m["group"] == "link" and "drive.google.com" in m["url"] for m in report["materials"]
            )
            assert all(m["contents_read"] is False for m in report["materials"])
            # an unrelated photo from another sender is only an unconfirmed candidate
            assert not any(m["group"] == "photo" for m in report["materials"])
            assert any(m["group"] == "photo" for m in report["candidates"])
            assert len(report["revisions"]) == 2

            # relative deadline is not a date, no invented subject, neutral cleaned title
            teams = next(
                c for c in cards if 9206 in {s["telegram_message_id"] for s in c["sources"]}
            )
            assert teams["deadline_at"] is None and "к следующей паре" in teams["when"].casefold()
            assert teams["subject"] is None
            assert (
                "#" not in teams["title"]
                and teams["title"] != "Учебное задание по предмету Культура"
            )

            # named weekday keeps its date
            exam = next(
                c for c in cards if 9207 in {s["telegram_message_id"] for s in c["sources"]}
            )
            assert exam["deadline_at"].astimezone(MSK).date() == wednesday.date()

            # the reschedule is never applied silently: it is a separate held proposal
            assert exam["status"] == "будет показана"
            change = next(c for c in cards if "unconfirmed_change" in c["reasons"])
            assert change["status"].startswith("удержана")
            assert {s["telegram_message_id"] for s in change["sources"]} == {9207, 9208}
            assert (
                change["deadline_at"].astimezone(MSK).date()
                == (wednesday + timedelta(days=2)).date()
            )
            assert exam["deadline_at"].astimezone(MSK).date() == wednesday.date()

            # the event is reported for the owner but is neither a card nor a product write
            assert not any(9210 in {s["telegram_message_id"] for s in c["sources"]} for c in cards)
            held = [h for h in preview["held_candidates"] if h["target_message_id"] == 9210]
            assert [(h["category"], h["title"]) for h in held] == [
                ("event", "Встреча с выпускниками")
            ]

            # personal assignment is never published to the group
            personal = next(
                c for c in cards if 9209 in {s["telegram_message_id"] for s in c["sources"]}
            )
            assert personal["status"].startswith("удержана")

            from studgroup.analysis.wave_results import wave_results

            async with AsyncSession(engine) as db:
                summary = await wave_results(db, now, wave_key="w-t")
                await db.rollback()
            assert await counts() == zero  # the summary is read-only too
            assert summary["status"] == "ok" and summary["runs_by_state"]["completed"] >= 7
            assert summary["proposals"]["accepted"] >= 6
            assert summary["held_candidates"]["by_category"] == {"event": 1}
            assert summary["cards"]["total"] == len(cards)
            assert summary["cards"]["multi_source"] >= 1
            assert summary["cards"]["with_confirmed_materials"] >= 1
            assert summary["cards"]["with_deadline"] >= 1
            assert summary["tokens"]["charged_usd"] is not None
            assert "кмк-секрет" not in str(summary)
            assert all(
                not isinstance(v, str) or len(v) < 80 for v in summary["proposals"]["by_kind"]
            )

            chunks = render_preview(preview, -1004400000001, 0, 5)
            text = "\n".join(chunks)
            assert all(len(chunk) <= 3500 for chunk in chunks)
            if os.environ.get("PREVIEW_DUMP"):
                Path(os.environ["PREVIEW_DUMP"]).write_text(text, encoding="utf-8")
            assert (
                "Файлы (1)" in text
                and "Ссылки (1)" in text
                and "содержимое файлов C+ не читал" in text
            )

            async with AsyncSession(engine) as db:
                group = await db.get(Group, group_id)
                group.pilot_authorized = False
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_preview_supplement_from_its_own_later_run_is_not_a_second_card():
    url = os.environ["POSTGRES_TEST_URL"]
    now = datetime.now(UTC).replace(microsecond=0)
    rows = [
        (
            9301,
            0,
            "Каждый делает доклад-презентацию о выдающихся героях РФ",
            HEADMAN_TELEGRAM_ID,
            None,
            [],
        ),
        (
            9302,
            240,
            "Темы: 1) Исторические деятели 2) Творцы 3) Учёные",
            HEADMAN_TELEGRAM_ID,
            9301,
            [],
        ),
        (
            9303,
            241,
            "",
            HEADMAN_TELEGRAM_ID,
            9301,
            [("document", "Критерии.pdf", "application/pdf", None)],
        ),
        (9304, 250, "Физика: решить задачи 1–3 до пятницы", STUDENT_TELEGRAM_ID, None, []),
    ]
    script = defaultdict(list)
    script[9301] = [
        card(
            None,
            "Подготовить доклад-презентацию о героях РФ",
            "Каждый готовит доклад-презентацию о выдающихся героях РФ.",
            [9301],
        )
    ]
    # the later run only saw its own message: it would have become a duplicate card
    script[9302] = [
        card(None, "Темы доклада", "Темы: 1) Исторические деятели 2) Творцы 3) Учёные", [9302])
    ]
    script[9304] = [
        card(
            "Физика",
            "Решить задачи 1–3",
            "Решить задачи 1–3.",
            [9304],
            at=(now.astimezone(MSK) + timedelta(days=2))
            .replace(hour=23, minute=59, second=0, microsecond=0)
            .isoformat(),
        )
    ]

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        settings = Settings(database_url=url, ai_enabled=False, ai_shadow_enabled=True)
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                await db.execute(
                    delete(User).where(
                        User.telegram_user_id.in_([HEADMAN_TELEGRAM_ID, STUDENT_TELEGRAM_ID])
                    )
                )
                db.add(
                    Group(
                        id=group_id,
                        telegram_chat_id=-1004400000000 - int(uuid.uuid4().int % 10**6),
                        name="Disposable supplement E2E",
                        timezone="Europe/Moscow",
                        pilot_authorized=True,
                        bot_added_at=now - timedelta(days=2),
                    )
                )
                headman = User(telegram_user_id=HEADMAN_TELEGRAM_ID, display_name="Староста")
                student = User(telegram_user_id=STUDENT_TELEGRAM_ID, display_name="Студент")
                db.add_all([headman, student])
                await db.flush()
                db.add_all(
                    [
                        Membership(
                            user_id=headman.id, group_id=group_id, role="headman", status="active"
                        ),
                        Membership(
                            user_id=student.id, group_id=group_id, role="student", status="active"
                        ),
                    ]
                )
                await db.flush()
                await configure_group_mode(
                    db,
                    group_id,
                    "shadow",
                    now - timedelta(days=1),
                    shadow_daily_budget_usd=Decimal("0.50"),
                    shadow_sample_percent=100,
                )
                await db.commit()
            start = now - timedelta(hours=8)

            async def seed(selected):
                async with AsyncSession(engine) as db:
                    for mid, minutes, text, sender, reply, attachments in selected:
                        stamp = start + timedelta(minutes=minutes)
                        row = RawMessage(
                            group_id=group_id,
                            telegram_message_id=mid,
                            text=text,
                            sender_id=sender,
                            reply_to_message_id=reply,
                            message_thread_id=591,
                            message_date=stamp,
                            version_date=stamp,
                            revision=1,
                            imported=False,
                            live_received_at=stamp,
                            delete_at=now + timedelta(days=30),
                        )
                        db.add(row)
                        await db.flush()
                        for index, (kind, name, mime, album) in enumerate(attachments):
                            db.add(
                                MessageAttachment(
                                    raw_message_id=row.id,
                                    group_id=group_id,
                                    source_revision=1,
                                    kind=kind,
                                    stable_file_id=f"{mid}-{index}",
                                    telegram_file_id=f"tg-{mid}-{index}",
                                    file_name=name,
                                    mime_type=mime,
                                    media_group_id=album,
                                    created_at=stamp,
                                    delete_at=now + timedelta(days=30),
                                )
                            )
                    await db.commit()

            common = {"group_id": group_id, "since": now - timedelta(days=1), "now": now}
            provider = ScriptedProvider(script)
            # The host run is frozen before the supplement exists, as in live operation.
            await seed([rows[0], rows[3]])
            async with AsyncSession(engine) as db:
                await backfill.enqueue_window(db, **common, limit=20, apply=True)
                await db.commit()
            for _ in range(4):
                await process_shadow_next(engine, settings, provider, now)
            await seed(rows[1:3])
            async with AsyncSession(engine) as db:
                await backfill.enqueue_window(db, **common, limit=20, apply=True)
                await db.commit()
            for _ in range(6):
                await process_shadow_next(engine, settings, provider, now)

            async def homework_count():
                async with AsyncSession(engine) as db:
                    return await db.scalar(
                        select(func.count())
                        .select_from(Homework)
                        .where(Homework.group_id == group_id)
                    )

            assert await homework_count() == 0
            async with AsyncSession(engine) as db:
                preview = await build_preview(db, **common)
                await db.rollback()
            assert await homework_count() == 0
            cards = preview["cards"]
            assert len(cards) == 2  # report (with its supplement) + physics, no third card
            report = next(
                c for c in cards if 9301 in {s["telegram_message_id"] for s in c["sources"]}
            )
            # the captionless PDF (a reply to the task) is a source too, added by the writer's
            # reconciliation even though its own run produced nothing
            assert sorted(s["telegram_message_id"] for s in report["sources"]) == [9301, 9302, 9303]
            # the supplement is a source, not pasted chat text; the card waits for AI integration
            assert "Дополнение" not in report["description"]
            assert "supplement_pending_integration" in report["reasons"]
            assert report["title"] == "Подготовить доклад-презентацию о героях РФ"
            assert [m["title"] for m in report["materials"] if m["group"] == "file"] == [
                "Критерии.pdf"
            ]
            assert len(report["revisions"]) == 2 and report["status"] == "будет показана"
            async with AsyncSession(engine) as db:
                again = await build_preview(db, **common)
                await db.rollback()
            assert [(c["title"], c["description"]) for c in again["cards"]] == [
                (c["title"], c["description"]) for c in cards
            ]
            async with AsyncSession(engine) as db:
                group = await db.get(Group, group_id)
                group.pilot_authorized = False
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
