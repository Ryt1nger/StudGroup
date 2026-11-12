"""A partial photo can support ONLY explicitly assigned, generic homework (no paid calls)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from studgroup.analysis.image_evidence import proposal_image_problem

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def message(mid, text, *, minute=0, topic=77, reply=None):
    return SimpleNamespace(
        telegram_message_id=mid,
        revision=1,
        text=text,
        topic_id=topic,
        reply_to_message_id=reply,
        message_date=NOW + timedelta(minutes=minute),
    )


def partial(mid=101, *, summary="Фото страницы с задачами", error="image_reading_incomplete"):
    return SimpleNamespace(
        attachment_id=uuid4(),
        telegram_message_id=mid,
        source_revision=1,
        state="failed",
        error=error,
        reading=None,
        partial_summary=summary,
    )


def proposed(*, title="ДЗ по фото", photo_mid=101, chat_mid=102):
    return SimpleNamespace(
        semantic_kind="homework",
        destination="task",
        title=title,
        evidence=[
            SimpleNamespace(telegram_message_id=photo_mid, revision=1, fact_paths=["description"]),
            SimpleNamespace(telegram_message_id=chat_mid, revision=1, fact_paths=["title"]),
        ],
    )


def test_partial_image_with_linked_homework_message_can_form_generic_card():
    data = SimpleNamespace(
        messages=[message(101, ""), message(102, "Домашнее задание на следующую пару", minute=2)],
        image_evidence=[partial()],
    )
    assert proposal_image_problem(proposed(), data) is None


def test_partial_image_is_not_homework_without_explicit_chat_obligation():
    data = SimpleNamespace(
        messages=[message(101, ""), message(102, "Вот конспект", minute=2)],
        image_evidence=[partial()],
    )
    assert proposal_image_problem(proposed(), data) == "image_evidence_unavailable"


def test_partial_image_cannot_claim_specific_unverified_exercises():
    data = SimpleNamespace(
        messages=[message(101, ""), message(102, "ДЗ по фото", minute=1)],
        image_evidence=[partial()],
    )
    assert proposal_image_problem(proposed(title="Решить задачи №5 и №6"), data) == "image_evidence_unavailable"


def test_partial_image_requires_actual_partial_vision_output():
    data = SimpleNamespace(
        messages=[message(101, ""), message(102, "ДЗ по фото", minute=1)],
        image_evidence=[partial(summary=None)],
    )
    assert proposal_image_problem(proposed(), data) == "image_evidence_unavailable"


def test_unrelated_chat_instruction_cannot_authorize_other_photo():
    data = SimpleNamespace(
        messages=[message(101, "", topic=11), message(102, "ДЗ по фото", minute=30, topic=12)],
        image_evidence=[partial()],
    )
    assert proposal_image_problem(proposed(), data) == "image_evidence_unavailable"


def test_temporary_download_failure_does_not_authorize_generic_homework():
    data = SimpleNamespace(
        messages=[message(101, "ДЗ по фото")],
        image_evidence=[partial(error="telegram_unavailable")],
    )
    assert proposal_image_problem(
        SimpleNamespace(
            semantic_kind="homework",
            destination="task",
            title="ДЗ по фото",
            evidence=[SimpleNamespace(telegram_message_id=101, revision=1, fact_paths=["description"])],
        ),
        data,
    ) == "image_evidence_unavailable"
