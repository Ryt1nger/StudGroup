import asyncio
from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, GroupAIProfile, GroupTopic, MessageAttachment, RawMessage

pytest_plugins = ["test_schedule_api"]


def delivery(update_id=1, chat_id=-1001, text="ДЗ по математике", edited=None):
    message = {
        "message_id": 42,
        "date": 1791200000,
        "chat": {"id": chat_id, "type": "supergroup"},
        "text": text,
        "from": {"id": 1},
    }
    if edited:
        message["edit_date"] = edited
    return {"update_id": update_id, "edited_message" if edited else "message": message}


def rows(client):
    async def read():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            return list((await db.scalars(select(RawMessage))).all())

    return asyncio.run(read())


def attachments(client):
    async def read():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            return list((await db.scalars(select(MessageAttachment))).all())

    return asyncio.run(read())


def send(client, payload):
    client.app.state.settings.telegram_webhook_secret = "test-secret"
    return client.post(
        "/v1/telegram/webhook",
        json=payload,
        headers={"X-Telegram-Bot-Api-Secret-Token": "test-secret"},
    )


def test_delivery_is_durable_and_idempotent(client):
    assert send(client, delivery()).status_code == 200
    assert send(client, delivery()).status_code == 200
    assert send(client, delivery(update_id=2)).status_code == 200
    saved = rows(client)
    assert len(saved) == 1
    assert saved[0].processing_state == "pending"
    assert saved[0].revision == 1


def test_attachment_caption_is_saved_for_semantic_analysis(client):
    payload = delivery(text=None)
    payload["message"]["caption"] = "матан 16, это из сборника"
    payload["message"]["document"] = {
        "file_id": "opaque",
        "file_unique_id": "stable-document",
        "file_name": "task.pdf",
        "mime_type": "application/pdf",
        "file_size": 1234,
    }
    assert send(client, payload).status_code == 200
    assert rows(client)[0].text == "матан 16, это из сборника"
    assert rows(client)[0].processing_state == "pending"
    saved = attachments(client)
    assert len(saved) == 1
    assert saved[0].kind == "document"
    assert saved[0].stable_file_id == "stable-document"
    assert saved[0].processing_state == "metadata_only"


def test_no_caption_photo_is_persisted_as_metadata_only_source(client):
    payload = delivery(text=None)
    payload["message"]["media_group_id"] = "album-1"
    payload["message"]["photo"] = [
        {
            "file_id": "small",
            "file_unique_id": "small-unique",
            "width": 90,
            "height": 90,
            "file_size": 100,
        },
        {
            "file_id": "large",
            "file_unique_id": "large-unique",
            "width": 1280,
            "height": 720,
            "file_size": 5000,
        },
    ]
    assert send(client, payload).status_code == 200
    saved_source = rows(client)[0]
    saved_attachment = attachments(client)[0]
    assert saved_source.text == ""
    assert saved_source.processing_state == "pending"
    assert saved_attachment.telegram_file_id == "large"
    assert saved_attachment.media_group_id == "album-1"
    assert saved_attachment.source_revision == 1


def test_attachment_edit_revisions_metadata_without_duplicate_active_rows(client):
    original = delivery(text=None)
    original["message"]["document"] = {
        "file_id": "old",
        "file_unique_id": "old-unique",
    }
    assert send(client, original).status_code == 200
    edited = delivery(update_id=2, text=None, edited=1791200100)
    edited["edited_message"]["document"] = {
        "file_id": "new",
        "file_unique_id": "new-unique",
    }
    assert send(client, edited).status_code == 200
    assert rows(client)[0].revision == 2
    saved = attachments(client)
    assert len(saved) == 2
    assert [(row.telegram_file_id, row.active) for row in saved] == [
        ("old", False),
        ("new", True),
    ]


def test_edit_updates_same_message_and_late_original_cannot_overwrite(client):
    send(client, delivery())
    send(client, delivery(update_id=2, text="Исправленное ДЗ", edited=1791200100))
    send(client, delivery(update_id=3, text="Устаревшее ДЗ"))
    saved = rows(client)
    assert len(saved) == 1
    assert saved[0].text == "Исправленное ДЗ"
    assert saved[0].revision == 2


def test_unconnected_group_does_not_store_text(client):
    assert send(client, delivery(chat_id=-999)).status_code == 200
    assert rows(client) == []


def test_invalid_secret_rejected(client):
    client.app.state.settings.telegram_webhook_secret = "expected"
    assert (
        client.post(
            "/v1/telegram/webhook",
            json=delivery(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        ).status_code
        == 401
    )
    assert rows(client) == []


def test_reply_reference_is_validated_and_saved(client):
    payload = delivery()
    payload["message"]["reply_to_message"] = {"message_id": 41, "text": "Original"}
    assert send(client, payload).status_code == 200
    assert rows(client)[0].reply_to_message_id == 41
    payload["message"]["reply_to_message"]["message_id"] = "invalid"
    assert send(client, payload).status_code == 422


def test_topic_identity_and_name_are_persisted_before_analysis(client):
    payload = delivery()
    payload["message"]["message_thread_id"] = 77
    payload["message"]["is_topic_message"] = True
    payload["message"]["forum_topic_created"] = {"name": "Математика"}
    assert send(client, payload).status_code == 200
    assert rows(client)[0].message_thread_id == 77

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            topic = await db.scalar(select(GroupTopic))
            profile = await db.scalar(select(GroupAIProfile))
            assert topic.telegram_thread_id == 77
            assert topic.name == "Математика"
            assert profile.state == "mapping"
            assert profile.backfill_requested

    asyncio.run(inspect())


def test_own_join_service_event_sets_exact_history_boundary(client):
    client.app.state.settings.telegram_bot_token = "777:secret"
    payload = delivery(text=None)
    payload["message"]["new_chat_members"] = [{"id": 777, "is_bot": True}]
    assert send(client, payload).status_code == 200

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            assert (
                int(group.bot_added_at.replace(tzinfo=UTC).timestamp())
                == payload["message"]["date"]
            )

    asyncio.run(inspect())
