"""University credentials stay encrypted and are visible only as connection state."""

import asyncio

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import RanepaAccount

pytest_plugins = ["test_schedule_api"]
AUTH = {"Authorization": "Bearer valid"}


def test_account_is_write_only_encrypted_and_revocable(client):
    url = "/v1/ranepa-account"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=AUTH).json() == {"connected": False, "updated_at": None}
    assert (
        client.put(url, json={"login": "student", "password": "pw"}, headers=AUTH).status_code
        == 503
    )
    key = Fernet.generate_key().decode()
    client.app.state.settings.profile_encryption_key = SecretStr(key)
    response = client.put(url, json={"login": "student", "password": "pw"}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["connected"] is True
    assert "student" not in response.text and "pw" not in response.text

    async def stored():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalars(select(RanepaAccount))).one()

    row = asyncio.run(stored())
    assert Fernet(key.encode()).decrypt(row.login_encrypted.encode()) == b"student"
    assert Fernet(key.encode()).decrypt(row.password_encrypted.encode()) == b"pw"
    assert "student" not in row.login_encrypted and "pw" not in row.password_encrypted
    assert client.get(url, headers=AUTH).json()["connected"] is True
    assert client.delete(url, headers=AUTH).status_code == 204
    assert client.get(url, headers=AUTH).json()["connected"] is False


def test_account_rejects_empty_and_unknown_fields(client):
    url = "/v1/ranepa-account"
    for body in (
        {"login": "", "password": "x"},
        {"login": "student", "password": " "},
        {"login": "student", "password": "x", "other": "x"},
    ):
        assert client.put(url, json=body, headers=AUTH).status_code == 422
