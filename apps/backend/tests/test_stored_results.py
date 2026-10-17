"""Stored shadow proposals in the OLD format must not leak wrong audience/date into any card."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from studgroup.analysis import backfill
from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import (
    AnalysisInput,
    ProposedAcademicItem,
    SourceMessage,
    ValidationResult,
)
from studgroup.analysis.preview import build_preview, reliability
from studgroup.analysis.stored import effective_validation
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    CanonicalAcademicItem,
    Group,
    Homework,
    Notification,
    RawMessage,
    ShadowEvaluationRun,
)

MSK = ZoneInfo("Europe/Moscow")
GROUP = UUID("00000000-0000-0000-0000-000000000009")
SENT = datetime(2026, 10, 12, 9, tzinfo=UTC)
TEAMS = "#орг к следующей паре определиться с командами на проект и темой"
REPORT = (
    "#орг Каждый делает доклад-презентацию о выдающихся героях (персоналиях) РФ. "
    "Например, учёные, выдающиеся исторические личности и тд."
)


def old_proposal(mid, **patch):
    """Shape of what the pre-fix pipeline stored: the model's fields, no later safeguards."""
    values = {
        "destination": "task",
        "semantic_kind": "homework",
        "subject": None,
        "title": "Учебное задание по предмету Культура",
        "description": "Определиться с командами и темой проекта.",
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "audience": {"scope": "group", "label": None},
        "confidence": 90,
        "attention_reasons": [],
        "evidence": [{"telegram_message_id": mid, "revision": 1, "fact_paths": ["title"]}],
    }
    return ProposedAcademicItem.model_validate(values | patch)


def stored(mid, text, proposal, **extra):
    """Round-trip through JSON exactly like the database column does."""
    message = SourceMessage(
        telegram_message_id=mid, revision=1, message_date=SENT, text=text, topic_id=591
    )
    data = AnalysisInput(
        group_id=GROUP,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[mid],
        messages=[message],
    )
    validation = ValidationResult(
        source_revision=1,
        accepted=[proposal],
        accepted_ordinals=[0],
        raw_proposal_count=1,
        explicit_empty=False,
    )
    return (
        AnalysisInput.model_validate_json(data.model_dump_json()),
        ValidationResult.model_validate_json(validation.model_dump_json()),
    )


def old_4575():
    at = datetime(2026, 10, 20, 0, 0, tzinfo=MSK)  # computed by the model, not written anywhere
    return stored(
        4575,
        TEAMS,
        old_proposal(4575, deadline={"at": at, "date_only": False, "basis": "explicit"}),
    )


def old_4597():
    return stored(
        4597,
        REPORT,
        old_proposal(
            4597,
            title="Доклад-презентация",
            audience={"scope": "personal", "label": None},
            description="Каждый делает доклад-презентацию о выдающихся героях РФ.",
        ),
    )


def test_old_relative_deadline_is_not_shown_as_an_explicit_date():
    data, validation = old_4575()
    assert validation.accepted[0].deadline.basis == "explicit"  # the stored (wrong) value
    effective = effective_validation(validation, data.messages, "Europe/Moscow")
    (proposal,) = effective.accepted
    assert proposal.deadline.at is None and proposal.deadline.basis == "unknown"
    assert "relative_deadline_unresolved" in proposal.attention_reasons
    assert effective.adapter_actions["unsupported_explicit_deadline_cleared"] == 1
    card = edit_card(proposal, data.messages)
    assert card.visibility == "headman_attention"
    # the model's own description is kept; the chat wording is not pasted into it ...
    assert card.description == "Определиться с командами и темой проекта."
    # ... and the relative wording stays visible next to the deadline in the owner's preview
    when, trust = reliability(
        proposal.deadline.at,
        proposal.deadline.date_only,
        proposal.deadline.basis,
        card.attention_reasons,
        MSK,
        [message.text for message in data.messages],
    )
    assert "к следующей паре" in when and "дата не подтверждена" in trust


def test_old_each_student_assignment_is_group_not_personal_held():
    data, validation = old_4597()
    assert validation.accepted[0].audience.scope == "personal"
    assert edit_card(validation.accepted[0], data.messages).visibility == "held"
    (proposal,) = effective_validation(validation, data.messages, "Europe/Moscow").accepted
    assert (proposal.audience.scope, proposal.audience.label) == ("group", None)
    card = edit_card(proposal, data.messages)
    assert (
        card.visibility == "normal"
        and "personal_not_group_publishable" not in card.attention_reasons
    )


def test_genuine_old_personal_assignment_and_real_dates_are_left_alone():
    personal = stored(
        1,
        "Иванов, принеси справку в деканат",
        old_proposal(
            1,
            title="Принести справку",
            description="Принести справку.",
            audience={"scope": "personal", "label": None},
        ),
    )
    (item,) = effective_validation(personal[1], personal[0].messages, "Europe/Moscow").accepted
    assert item.audience.scope == "personal"
    assert edit_card(item, personal[0].messages).visibility == "held"
    dated = stored(
        2,
        "Сдать отчёт до 20.10",
        old_proposal(
            2,
            title="Сдать отчёт",
            description="Сдать отчёт.",
            deadline={
                "at": datetime(2026, 10, 20, 0, 0, tzinfo=MSK),
                "date_only": True,
                "basis": "explicit",
            },
        ),
    )
    (item,) = effective_validation(dated[1], dated[0].messages, "Europe/Moscow").accepted
    assert item.deadline.at == datetime(2026, 10, 20, 0, 0, tzinfo=MSK)
    assert item.deadline.basis == "explicit"


def test_validation_is_not_weakened_conflicting_literal_date_is_still_serious():
    wrong = stored(
        3,
        "Сдать отчёт до 20.10",
        old_proposal(
            3,
            title="Сдать отчёт",
            description="Сдать отчёт.",
            deadline={
                "at": datetime(2026, 10, 25, 0, 0, tzinfo=MSK),
                "date_only": True,
                "basis": "explicit",
            },
        ),
    )
    (item,) = effective_validation(wrong[1], wrong[0].messages, "Europe/Moscow").accepted
    assert "conflicting_deadlines" in item.attention_reasons
    assert edit_card(item, wrong[0].messages).visibility == "held"


def test_effective_view_is_idempotent_and_keeps_sources():
    for build in (old_4575, old_4597):
        data, validation = build()
        once = effective_validation(validation, data.messages, "Europe/Moscow")
        twice = effective_validation(once, data.messages, "Europe/Moscow")
        assert once.accepted == twice.accepted
        assert once.accepted[0].evidence == validation.accepted[0].evidence


pytestmark_pg = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


@pytestmark_pg
def test_postgres_preview_review_promote_and_writer_all_use_the_effective_view():
    url = os.environ["POSTGRES_TEST_URL"]
    now = datetime.now(UTC).replace(microsecond=0)
    rows = [(4575, TEAMS, old_4575), (4597, REPORT, old_4597)]

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                db.add(
                    Group(
                        id=group_id,
                        telegram_chat_id=-1004400000000 - int(uuid.uuid4().int % 10**6),
                        name="Disposable stored E2E",
                        timezone="Europe/Moscow",
                        pilot_authorized=True,
                        bot_added_at=now - timedelta(days=3),
                    )
                )
                await db.flush()
                for index, (mid, text, build) in enumerate(rows):
                    stamp = now - timedelta(hours=6 - index)
                    raw = RawMessage(
                        group_id=group_id,
                        telegram_message_id=mid,
                        text=text,
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
                    data, validation = build()
                    message = data.messages[0].model_copy(update={"message_date": stamp})
                    data = data.model_copy(update={"messages": [message], "group_id": group_id})
                    db.add(
                        ShadowEvaluationRun(
                            group_id=group_id,
                            raw_message_id=raw.id,
                            source_revision=1,
                            analysis_generation=raw.analysis_generation,
                            source_snapshot_sha256="b" * 64,
                            engine_version="cplus-shadow-1",
                            state="completed",
                            input_json=data.model_dump_json(),
                            validation_result_json=validation.model_dump_json(),
                            metrics_json="{}",
                            created_at=stamp,
                            completed_at=stamp,
                            delete_at=now + timedelta(days=30),
                        )
                    )
                await db.commit()
            common = {"group_id": group_id, "since": now - timedelta(days=1), "now": now}

            async def counts():
                async with AsyncSession(engine) as db:
                    result = {}
                    for model in (Homework, AcademicDeadline, Notification, CanonicalAcademicItem):
                        result[model.__tablename__] = await db.scalar(
                            select(func.count())
                            .select_from(model)
                            .where(model.group_id == group_id)
                        )
                    result["outbox"] = await db.scalar(select(func.count()).select_from(BotOutbox))
                    return result

            zero = await counts()
            async with AsyncSession(engine) as db:
                preview = await build_preview(db, **common)
                await db.rollback()
            by_source = {c["sources"][0]["telegram_message_id"]: c for c in preview["cards"]}
            teams, report = by_source[4575], by_source[4597]
            assert teams["deadline_at"] is None and teams["trust"].startswith("относительный")
            assert "«к следующей паре»" in teams["when"]
            assert report["audience"]["scope"] == "group" and report["status"] == "будет показана"
            async with AsyncSession(engine) as db:
                reviewed = await backfill.review(db, **common)
                await db.rollback()
            by_review = {c["target_message_id"]: c for c in reviewed}
            assert by_review[4575]["deadline_at"] is None
            assert by_review[4597]["audience"]["scope"] == "group"
            assert by_review[4597]["visibility"] == "normal"
            assert await counts() == zero

            # the writer itself, fed an old-format validation directly (live/new-mode path)
            from studgroup.analysis.writer import apply_validated_batch

            old_data, old_validation = old_4597()
            old_message = old_data.messages[0].model_copy(update={"message_date": now})
            async with AsyncSession(engine) as db:
                batch = await apply_validated_batch(
                    db,
                    group_id=group_id,
                    validation=old_validation,
                    messages=[old_message],
                    now=now,
                    emit_notifications=False,
                )
                assert [o.application_status for o in batch.outcomes] == ["created"]
                item = await db.scalar(
                    select(CanonicalAcademicItem).where(CanonicalAcademicItem.group_id == group_id)
                )
                assert item.audience_scope == "group"
                await db.rollback()
            assert await counts() == zero

            # promote (what would be published, and what the Mini App would read)
            async with AsyncSession(engine) as db:
                done = await backfill.promote(db, **common, limit=10, apply=True)
                homework = (
                    await db.scalars(select(Homework).where(Homework.group_id == group_id))
                ).all()
                items = (
                    await db.scalars(
                        select(CanonicalAcademicItem).where(
                            CanonicalAcademicItem.group_id == group_id
                        )
                    )
                ).all()
                assert done["outcomes"].get("created") == 2, done
                assert all(h.deadline_at is None for h in homework)
                assert {i.audience_scope for i in items} == {"group"}
                assert {i.deadline_basis for i in items} == {"unknown"}
                assert all(
                    h.verification_state != "from_group_message" or h.deadline_at is None
                    for h in homework
                )
                notes = await db.scalar(
                    select(func.count())
                    .select_from(Notification)
                    .where(Notification.group_id == group_id)
                )
                assert notes == 0
                await db.rollback()  # nothing is kept
            assert await counts() == zero
            async with AsyncSession(engine) as db:
                group = await db.get(Group, group_id)
                group.pilot_authorized = False
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_json_shape_of_old_results_is_what_the_database_stores():
    _, validation = old_4597()
    raw = json.loads(validation.model_dump_json())
    assert raw["accepted"][0]["audience"] == {"scope": "personal", "label": None}
