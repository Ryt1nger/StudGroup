from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from studgroup.academic_context import build_context

BASE = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def message(mid, text, *, minutes=0, topic=None, reply_to=None):
    return SimpleNamespace(
        message_id=mid,
        reply_to_message_id=reply_to,
        message_date=BASE + timedelta(minutes=minutes),
        text=text,
        message_thread_id=topic,
        topic_name=f"Топик {topic}" if topic is not None else None,
    )


def ids(context):
    return [item["message_id"] for item in context]


def test_topic_target_uses_same_topic_for_ordinary_neighborhood():
    context = build_context(
        [
            message(1, "сосед из другого топика", minutes=-1, topic=20),
            message(2, "#матан предел функции", topic=10),
            message(3, "продолжение в том же топике", minutes=1, topic=10),
            message(4, "ещё один чужой сосед", minutes=2, topic=30),
        ],
        2,
        neighborhood=8,
    )

    assert ids(context) == [2, 3]
    assert context[0]["topic_id"] == 10
    assert context[0]["topic_name"] == "Топик 10"


def test_topic_target_can_use_explicit_cross_topic_subject_or_reply():
    context = build_context(
        [
            message(10, "#русский", topic=100),
            message(11, "#русский ДЗ по ПИР", minutes=1, topic=200),
            message(12, "уточнение без хештега", minutes=2, topic=300, reply_to=10),
            message(13, "случайная болтовня", minutes=3, topic=300),
        ],
        10,
        neighborhood=8,
    )

    assert ids(context) == [10, 11, 12]


def test_group_without_topics_keeps_global_neighborhood_fallback():
    context = build_context(
        [
            message(20, "сообщение до", minutes=-1),
            message(21, "домашнее задание", minutes=0),
            message(22, "сообщение после", minutes=1),
        ],
        21,
        neighborhood=8,
    )

    assert ids(context) == [20, 21, 22]
