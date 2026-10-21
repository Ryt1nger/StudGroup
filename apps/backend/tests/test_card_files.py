"""Files that stay in Telegram: headman adds one through the bot, students open it from the card."""

# ruff: noqa: F811
import re
import time
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from test_headman import click, deliver, foreman, last, run  # noqa: F401
from test_homework import seed_card
from test_ingestion import send

from studgroup import card_edit, telegram_files
from studgroup.models import MaterialLink

pytest_plugins = ["test_schedule_api"]
HEADERS = {"Authorization": "Bearer valid"}
FILE_ID = "BQACAgIAAxkBAAIB-secret-file-id"


def send_file(client, document=None, photo=None, caption=None):
    client.app.state._st_test_update = getattr(client.app.state, "_st_test_update", 8000) + 1
    ident = client.app.state._st_test_update
    message = {
        "message_id": ident,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1},
    }
    if document:
        message["document"] = document
    if photo:
        message["photo"] = photo
    if caption:
        message["caption"] = caption
    assert send(client, {"update_id": ident, "message": message}).status_code == 200


def attach_through_bot(client, card, document=None, **kwargs):
    deliver(client, f"/start file_h_{card}")
    assert "Пришли файл" in last(client)["text"]
    send_file(
        client,
        document or {"file_id": FILE_ID, "file_name": "Лекция 3.pdf", "file_size": 1000},
        **kwargs,
    )
    assert "Лекция 3.pdf" in last(client)["text"]
    click(client, "Подтвердить")


def stored(client):
    async def read(db):
        return (await db.scalars(select(MaterialLink))).all()

    return run(client, read)


def test_file_reference_round_trip_and_signature():
    telegram_files.configure("secret")
    url = telegram_files.encode("document", 1000, FILE_ID)
    assert telegram_files.decode(url) == ("document", 1000, FILE_ID)
    assert telegram_files.decode("https://example.com/a") is None
    material = "11111111-1111-4111-8111-111111111111"
    path = telegram_files.signed_path(material)
    exp, sig = re.search(r"exp=(\d+)&sig=(\w+)", path).groups()
    assert telegram_files.verify(material, int(exp), sig)
    assert not telegram_files.verify(material, int(exp), "0" * 64)
    assert not telegram_files.verify("22222222-2222-4222-8222-222222222222", int(exp), sig)
    assert not telegram_files.verify(material, int(time.time()) - 1, sig)


def test_headman_attaches_a_file_and_students_get_a_link_without_the_telegram_id(foreman):
    card = seed_card(foreman)
    attach_through_bot(foreman, card)
    [row] = stored(foreman)
    assert row.entity_key == f"h:{card}" and row.title == "Лекция 3.pdf"
    assert telegram_files.decode(row.url) == ("document", 1000, FILE_ID)

    detail = foreman.get(f"/v1/homework/{card}", headers=HEADERS).json()
    [material] = detail["materials"]
    assert material["kind"] == "document" and material["file_name"] == "Лекция 3.pdf"
    assert material["size_bytes"] == 1000 and material["mime_type"] == "application/pdf"
    assert material["url"].startswith("/v1/files/") and FILE_ID not in material["url"]


def test_photo_uses_the_largest_size_and_caption_as_title(foreman):
    card = seed_card(foreman)
    deliver(foreman, f"/start file_h_{card}")
    send_file(
        foreman,
        photo=[
            {"file_id": "small", "file_size": 100},
            {"file_id": "large", "file_size": 9000},
        ],
        caption="Доска после пары",
    )
    click(foreman, "Подтвердить")
    [row] = stored(foreman)
    assert row.title == "Доска после пары"
    assert telegram_files.decode(row.url) == ("photo", 9000, "large")


def test_oversized_file_is_refused_and_nothing_is_stored(foreman):
    card = seed_card(foreman)
    deliver(foreman, f"/start file_h_{card}")
    send_file(foreman, {"file_id": "big", "file_name": "film.mkv", "file_size": 25 * 1024 * 1024})
    assert "20 МБ" in last(foreman)["text"]
    assert stored(foreman) == []


def test_deep_link_for_a_non_headman_is_refused(client):
    card = seed_card(client)
    deliver(client, f"/start file_h_{card}")
    assert "нет прав" in last(client)["text"]
    send_file(client, {"file_id": FILE_ID, "file_name": "x.pdf", "file_size": 10})
    assert stored(client) == []


def test_download_streams_from_telegram_and_rejects_bad_links(foreman, monkeypatch):
    card = seed_card(foreman)
    attach_through_bot(foreman, card)
    [material] = foreman.get(f"/v1/homework/{card}", headers=HEADERS).json()["materials"]

    def telegram(request):
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "documents/f.pdf"}}
            )
        return httpx.Response(200, content=b"%PDF-1.4 body")

    real = httpx.AsyncClient
    monkeypatch.setattr(
        telegram_files.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(telegram)),
    )
    ok = foreman.get(material["url"])
    assert ok.status_code == 200 and ok.content == b"%PDF-1.4 body"
    assert ok.headers["content-type"] == "application/pdf"
    assert ok.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in ok.headers["cache-control"]

    forged = re.sub(r"sig=\w+", "sig=" + "0" * 64, material["url"])
    assert foreman.get(forged).status_code == 404
    expired = re.sub(r"exp=\d+", "exp=1", material["url"])
    assert foreman.get(expired).status_code == 404


def test_upload_link_is_for_editors_only(client, monkeypatch):
    card = seed_card(client)
    assert client.get(f"/v1/homework/{card}/file-link", headers=HEADERS).status_code == 403

    async def username(settings):
        return "studgroup_test_bot"

    monkeypatch.setattr(card_edit, "bot_username", username)
    from test_card_edit import set_role

    set_role(client, "headman")
    link = client.get(f"/v1/homework/{card}/file-link", headers=HEADERS)
    assert link.status_code == 200
    assert link.json() == {"url": f"https://t.me/studgroup_test_bot?start=file_h_{card}"}
