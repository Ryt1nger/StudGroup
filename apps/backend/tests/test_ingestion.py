import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import RawMessage

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
    payload["message"]["document"] = {"file_id": "opaque"}
    assert send(client, payload).status_code == 200
    assert rows(client)[0].text == "матан 16, это из сборника"
    assert rows(client)[0].processing_state == "pending"


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
