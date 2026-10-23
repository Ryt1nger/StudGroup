"""The caller's own profile: contact data, study platform and notification preferences."""

import asyncio

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import UserProfile

pytest_plugins = ["test_schedule_api"]
HEADERS = {"Authorization": "Bearer valid"}


def test_an_empty_profile_has_calm_defaults_and_needs_a_session(client):
    assert client.get("/v1/profile").status_code in (401, 422)
    data = client.get("/v1/profile", headers=HEADERS).json()
    assert data["first_name"] is None and data["sdu"]["password_set"] is False
    assert data["settings"]["enabled"] is True and data["settings"]["lessons"] is False


def test_contact_data_and_study_platform_are_saved_and_validated(client):
    saved = client.patch(
        "/v1/profile",
        json={
            "first_name": "  Тимофей ",
            "email": "t@example.com",
            "sdu_platform": "Moodle",
            "sdu_url": "https://sdo.example.edu",
            "sdu_login": "timofey",
            "sdu_calendar_url": "https://sdo.example.edu/cal.ics",
        },
        headers=HEADERS,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["first_name"] == "Тимофей" and body["sdu"]["login"] == "timofey"
    assert client.get("/v1/profile", headers=HEADERS).json() == body
    for bad in (
        {"email": "not-mail"},
        {"sdu_url": "http://sdo.example.edu"},
        {"sdu_calendar_url": "https://user:pw@sdo.example.edu/cal.ics"},
        {"first_name": "x" * 61},
    ):
        assert client.patch("/v1/profile", json=bad, headers=HEADERS).status_code == 422
    assert client.patch("/v1/profile", json={}, headers=HEADERS).status_code == 422
    assert client.patch("/v1/profile", json={"unknown": 1}, headers=HEADERS).status_code == 422


def test_the_study_platform_password_is_encrypted_and_never_returned(client):
    refused = client.patch("/v1/profile", json={"sdu_password": "secret-1"}, headers=HEADERS)
    assert refused.status_code == 503  # no encryption key configured
    key = Fernet.generate_key().decode()
    client.app.state.settings.profile_encryption_key = SecretStr(key)
    saved = client.patch("/v1/profile", json={"sdu_password": "secret-1"}, headers=HEADERS)
    assert saved.status_code == 200 and saved.json()["sdu"]["password_set"] is True
    assert "secret-1" not in saved.text and "password" not in client.get(
        "/v1/profile", headers=HEADERS
    ).text.replace("password_set", "")

    async def stored():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalar(select(UserProfile))).sdu_password_encrypted

    token = asyncio.run(stored())
    assert (
        token
        and "secret-1" not in token
        and Fernet(key.encode()).decrypt(token.encode()) == b"secret-1"
    )
    assert (
        client.patch("/v1/profile", json={"sdu_password": ""}, headers=HEADERS).status_code == 422
    )
    cleared = client.patch("/v1/profile", json={"clear_sdu_password": True}, headers=HEADERS)
    assert cleared.json()["sdu"]["password_set"] is False


def test_notification_preferences_are_stored_and_validated(client):
    ok = client.patch(
        "/v1/profile",
        json={"settings": {"enabled": False, "lessons": True, "lesson_lead_minutes": 30}},
        headers=HEADERS,
    )
    assert ok.status_code == 200 and ok.json()["settings"]["lesson_lead_minutes"] == 30
    assert ok.json()["settings"]["enabled"] is False
    bad = client.patch(
        "/v1/profile", json={"settings": {"lesson_lead_minutes": 7}}, headers=HEADERS
    )
    assert bad.status_code == 422


def test_the_browser_may_send_patch_from_the_web_app(client):
    """Without PATCH in the CORS methods the preflight fails and profile/card edits never reach the API."""
    origin = client.app.state.settings.webapp_origin
    response = client.options(
        "/v1/profile",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    allowed = response.headers["access-control-allow-methods"]
    assert "PATCH" in allowed and "DELETE" in allowed
