"""Owner-only CAPTCHA: signed Telegram access and one-use live answer."""

import asyncio
import base64
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from urllib.parse import urlencode

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import ranepa_manual_captcha as bridge
from studgroup.models import Group, RanepaCrawlJob, RanepaManualCaptcha, User

pytest_plugins = ["test_schedule_api"]


def telegram_data(user_id, token):
    data = {
        "auth_date": str(int(datetime.now(UTC).timestamp())),
        "user": json.dumps({"id": user_id, "first_name": "Владелец"}, ensure_ascii=False),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    key = hmac.digest(b"WebAppData", token.encode(), "sha256")
    data["hash"] = hmac.new(key, check.encode(), "sha256").hexdigest()
    return urlencode(data)


def png():
    image = Image.new("RGB", (120, 120), "blue")
    target = BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()


def make_challenge(client, expired=False):
    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            group = (await db.scalars(select(Group))).first()
            student = (await db.scalars(select(User))).first()
            job = RanepaCrawlJob(
                id=uuid.uuid4(),
                group_id=group.id,
                user_id=student.id,
                week_start=datetime(2026, 11, 9, tzinfo=UTC).date(),
                state="running",
                priority=0,
                attempts=1,
                created_at=datetime.now(UTC),
            )
            challenge = RanepaManualCaptcha(
                id=uuid.uuid4(),
                job_id=job.id,
                attempt=1,
                owner_telegram_id=1,
                state="pending",
                screenshot_base64=bridge._b64(png()),
                left_image_base64=bridge._b64(png()),
                right_image_base64=bridge._b64(png()),
                created_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=-1 if expired else 6),
            )
            db.add_all([job, challenge])
            challenge_id = str(challenge.id)
            await db.commit()
            return challenge_id

    return asyncio.run(seed())


def test_private_captcha_full_view_and_one_time_answer(client):
    client.app.state.settings.telegram_bot_token = "123:test-token"
    ident = make_challenge(client)
    page = client.get("/ranepa-captcha/" + ident)
    assert page.status_code == 200
    assert "полная головоломка" in page.text
    assert "no-store" in page.headers["cache-control"]
    path = "/v1/ranepa-captcha/" + ident
    assert client.post(path + "/state", json={"init_data": "fake"}).status_code == 401
    stranger = telegram_data(42, "123:test-token")
    assert client.post(path + "/state", json={"init_data": stranger}).status_code == 403
    owner = telegram_data(1, "123:test-token")
    response = client.post(path + "/state", json={"init_data": owner})
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["screenshot"].startswith("data:image/png;base64,")
    assert base64.b64decode(response.json()["left"].split(",")[1]) == png()
    assert (
        client.post(
            path + "/answer",
            json={"init_data": owner, "angle_left": 400, "angle_right": 210},
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/answer",
            json={"init_data": stranger, "angle_left": 30, "angle_right": 210},
        ).status_code
        == 403
    )
    assert (
        client.post(
            path + "/answer",
            json={"init_data": owner, "angle_left": 30, "angle_right": 210},
        ).status_code
        == 200
    )
    assert (
        client.post(
            path + "/answer",
            json={"init_data": owner, "angle_left": 80, "angle_right": 180},
        ).status_code
        == 409
    )
    assert client.post(path + "/state", json={"init_data": owner}).json()["left"] is None


def test_expired_captcha_never_shares_images_or_accepts_answer(client):
    client.app.state.settings.telegram_bot_token = "123:test-token"
    ident = make_challenge(client, expired=True)
    owner = telegram_data(1, "123:test-token")
    path = "/v1/ranepa-captcha/" + ident
    body = client.post(path + "/state", json={"init_data": owner}).json()
    assert body["status"] == "expired"
    assert body["screenshot"] is None
    assert (
        client.post(
            path + "/answer",
            json={"init_data": owner, "angle_left": 30, "angle_right": 210},
        ).status_code
        == 409
    )


def test_owner_message_after_three_solver_failures_is_actionable_and_private():
    text = bridge._manual_request_text(
        student_name="Тестовая\nучётка",
        group_code="Э-ОБ-385-04-26-03",
        auto_attempts=3,
    )
    assert "не завершился из-за CAPTCHA" in text
    assert "3 из 3 попыток" in text
    assert "Три автоматические попытки не подтвердили решение" in text
    assert "Пройти CAPTCHA" in text
    assert "6 минут" in text
    assert "Тестовая\n" not in text
    assert "Тестовая учётка" in text
    limited = bridge._manual_request_text(student_name=None, group_code=None, auto_attempts=1)
    assert "1 из 3 попыток" in limited
    assert "Остальные попытки не выполнены" in limited
    assert "Три автоматические попытки не подтвердили" not in limited
