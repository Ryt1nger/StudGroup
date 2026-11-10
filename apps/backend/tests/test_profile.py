"""The caller's own profile: contact data, study platform and notification preferences."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import create_app
from studgroup.models import Membership, User, UserProfile, WebSession
from studgroup.security import token_hash

pytest_plugins = ["test_schedule_api"]
HEADERS = {"Authorization": "Bearer valid"}


def approve_legal_documents(client):
    settings = client.app.state.settings
    settings.legal_operator_name = "ООО Тестовый оператор"
    settings.legal_operator_address = "Москва, тестовый адрес"
    settings.legal_operator_contact = "privacy@example.test"
    settings.legal_data_region = "Российская Федерация, Москва"
    settings.legal_documents_approved = True


def accept_study_credentials(client):
    approve_legal_documents(client)
    client.app.state.settings.ranepa_legacy_credentials_allowed = True
    if not client.app.state.settings.profile_encryption_key.get_secret_value():
        client.app.state.settings.profile_encryption_key = SecretStr(Fernet.generate_key().decode())
    from studgroup.legal import VERSION

    for purpose in ("terms", "service", "sdo"):
        result = client.post(
            "/v1/legal/consents",
            json={"purpose": purpose, "version": VERSION, "confirmed": True},
            headers=HEADERS,
        )
        assert result.status_code == 200, result.text


def test_an_empty_profile_has_calm_defaults_and_needs_a_session(client):
    assert client.get("/v1/profile").status_code == 401
    bad = client.get("/v1/profile", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "session_expired"
    assert client.patch("/v1/profile", json={"first_name": "А"}).status_code == 401
    data = client.get("/v1/profile", headers=HEADERS).json()
    assert data["first_name"] is None and data["sdu"]["password_set"] is False
    assert data["settings"]["enabled"] is True and data["settings"]["lessons"] is False


def test_contact_data_and_study_platform_are_saved_and_validated(client):
    accept_study_credentials(client)
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
    accept_study_credentials(client)
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


def test_legal_documents_are_public_and_individual_confirmations_are_audited(client):
    from studgroup.legal import VERSION

    public = client.get("/v1/legal/documents/sdo")
    assert public.status_code == 503
    assert "[ОПЕРАТОР НЕ УКАЗАН]" not in public.text

    assert client.get("/v1/legal/status").status_code == 401
    not_ready = client.post(
        "/v1/legal/consents",
        json={"purpose": "sdo", "version": VERSION, "confirmed": True},
        headers=HEADERS,
    )
    assert not_ready.status_code == 503

    approve_legal_documents(client)
    public = client.get("/v1/legal/documents/sdo")
    assert public.status_code == 200
    assert "СДО / ЛК" in public.text
    assert "secret-1" not in public.text
    assert client.get("/v1/legal/status", headers=HEADERS).json()["credentials_ready"] is False
    without_terms = client.post(
        "/v1/legal/consents",
        json={"purpose": "sdo", "version": VERSION, "confirmed": True},
        headers=HEADERS,
    )
    assert without_terms.status_code == 403
    for purpose in ("terms", "service"):
        assert (
            client.post(
                "/v1/legal/consents",
                json={"purpose": purpose, "version": VERSION, "confirmed": True},
                headers=HEADERS,
            ).status_code
            == 200
        )
    no_key = client.post(
        "/v1/legal/consents",
        json={"purpose": "sdo", "version": VERSION, "confirmed": True},
        headers=HEADERS,
    )
    assert no_key.status_code == 503
    wrong_version = client.post(
        "/v1/legal/consents",
        json={"purpose": "terms", "version": "obsolete", "confirmed": True},
        headers=HEADERS,
    )
    assert wrong_version.status_code == 409
    accept_study_credentials(client)
    status = client.get("/v1/legal/status", headers=HEADERS).json()
    assert status["ready"] is True
    assert status["terms"] and status["service"] and status["sdo"]
    assert client.get("/v1/legal/documents/privacy").status_code == 200

    async def evidence():
        from studgroup.models import LegalConsentEvent

        async with AsyncSession(client.app.state.engine) as db:
            items = (await db.scalars(select(LegalConsentEvent))).all()
            return [
                (event.purpose, event.action, event.document_sha256, event.source)
                for event in items
            ]

    proof = asyncio.run(evidence())
    assert len(proof) == 3  # repeated acceptance of the same version is idempotent
    assert {purpose for purpose, _, _, _ in proof} == {"terms", "service", "sdo"}
    assert all(len(digest) == 64 and source == "webapp" for _, _, digest, source in proof)


def test_study_credentials_need_separate_consent_even_with_encryption(client):
    client.app.state.settings.profile_encryption_key = SecretStr(Fernet.generate_key().decode())
    approve_legal_documents(client)
    result = client.patch(
        "/v1/profile",
        json={"sdu_login": "student", "sdu_password": "must-not-store"},
        headers=HEADERS,
    )
    assert result.status_code == 403
    assert "must-not-store" not in result.text

    accept_study_credentials(client)
    saved = client.patch(
        "/v1/profile",
        json={"sdu_login": "student", "sdu_password": "student-secret"},
        headers=HEADERS,
    )
    assert saved.status_code == 200
    assert "student-secret" not in saved.text
    assert saved.json()["sdu"]["password_set"] is True

    revoked = client.delete("/v1/legal/consents/sdo", headers=HEADERS)
    assert revoked.status_code == 200
    assert revoked.json()["credentials_deleted"]
    assert client.get("/v1/legal/status", headers=HEADERS).json()["sdo"] is False
    result = client.get("/v1/profile", headers=HEADERS).json()
    assert result["sdu"]["login"] is None and result["sdu"]["password_set"] is False
    assert (
        client.patch("/v1/profile", json={"sdu_login": "again"}, headers=HEADERS).status_code == 403
    )


def test_synthetic_credentials_survive_restart_and_stay_personal(client, caplog):
    key = Fernet.generate_key().decode()
    client.app.state.settings.profile_encryption_key = SecretStr(key)
    accept_study_credentials(client)
    original = "test-only-password-1"
    replacement = "test-only-password-2"
    saved = client.patch(
        "/v1/profile",
        json={"sdu_login": "test-student", "sdu_password": original},
        headers=HEADERS,
    )
    assert saved.status_code == 200 and saved.json()["sdu"]["password_set"]

    async def stored():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(
                select(UserProfile).where(UserProfile.sdu_login == "test-student")
            )
            return row.sdu_password_encrypted if row else None

    first_ciphertext = asyncio.run(stored())
    assert first_ciphertext and original not in first_ciphertext
    assert Fernet(key.encode()).decrypt(first_ciphertext.encode()) == original.encode()

    # A fresh app instance reads the same disk-backed database, as after a server restart.
    with TestClient(create_app(client.app.state.settings)) as restarted:
        reread = restarted.get("/v1/profile", headers=HEADERS)
        assert reread.status_code == 200
        assert reread.json()["sdu"]["login"] == "test-student"
        assert reread.json()["sdu"]["password_set"] is True
        assert original not in reread.text
        changed = restarted.patch(
            "/v1/profile",
            json={"sdu_login": "test-student", "sdu_password": replacement},
            headers=HEADERS,
        )
        assert changed.status_code == 200 and replacement not in changed.text

    second_ciphertext = asyncio.run(stored())
    assert second_ciphertext != first_ciphertext
    assert Fernet(key.encode()).decrypt(second_ciphertext.encode()) == replacement.encode()

    async def seed_other_user():
        async with AsyncSession(client.app.state.engine) as db:
            first = await db.scalar(select(Membership).where(Membership.status == "active"))
            other = User(id=uuid.uuid4(), telegram_user_id=9002, display_name="Test other")
            db.add(other)
            await db.flush()
            member = Membership(
                id=uuid.uuid4(),
                group_id=first.group_id,
                user_id=other.id,
                role="student",
                status="active",
            )
            db.add(member)
            db.add(
                WebSession(
                    token_hash=token_hash("other-test-token"),
                    user_id=other.id,
                    membership_id=member.id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await db.commit()

    asyncio.run(seed_other_user())
    other = client.get("/v1/profile", headers={"Authorization": "Bearer other-test-token"})
    assert other.status_code == 200
    assert other.json()["sdu"]["login"] is None
    assert other.json()["sdu"]["password_set"] is False
    assert client.get("/v1/profile", headers={"Authorization": "Bearer expired"}).status_code == 401

    revoked = client.delete("/v1/legal/consents/sdo", headers=HEADERS)
    assert revoked.status_code == 200 and revoked.json()["credentials_deleted"]

    async def cleared():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(UserProfile).where(UserProfile.sdu_login.is_(None)))
            return row.sdu_password_encrypted if row else "missing"

    assert asyncio.run(cleared()) is None
    assert original not in caplog.text and replacement not in caplog.text


def test_ydb_credential_path_keeps_main_database_empty_and_confirms_revocation(client, monkeypatch):
    accept_study_credentials(client)
    records = {}

    class FakeStore:
        async def get(self, key):
            return records.get(key)

        async def put(self, key, login, ciphertext, version):
            records[key] = (login, ciphertext, version)

        async def delete(self, key):
            records.pop(key, None)

    store = FakeStore()
    monkeypatch.setattr("studgroup.profile.configured_store", lambda _: store)
    monkeypatch.setattr("studgroup.legal.configured_store", lambda _: store)
    saved = client.patch(
        "/v1/profile",
        json={"sdu_login": "pilot-user", "sdu_password": "synthetic-password"},
        headers=HEADERS,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["sdu"]["login"] == "pilot-user"
    assert saved.json()["sdu"]["password_set"]

    async def stored():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(UserProfile))
            return row.sdu_login, row.sdu_password_encrypted, row.sdu_credential_id

    login, encrypted, pointer = asyncio.run(stored())
    assert login is None and encrypted is None and pointer in records
    assert client.get("/v1/profile", headers=HEADERS).json()["sdu"]["login"] == "pilot-user"
    revoked = client.delete("/v1/legal/consents/sdo", headers=HEADERS)
    assert revoked.status_code == 200 and revoked.json()["credentials_deleted"]
    assert pointer not in records
    assert asyncio.run(stored()) == (None, None, None)


def test_ydb_write_error_does_not_claim_password_saved(client, monkeypatch):
    from studgroup.ranepa_credential_store import CredentialStoreUnavailable

    accept_study_credentials(client)

    class FailedStore:
        async def put(self, *_args):
            raise CredentialStoreUnavailable()

    monkeypatch.setattr("studgroup.profile.configured_store", lambda _: FailedStore())
    result = client.patch(
        "/v1/profile",
        json={"sdu_login": "pilot-user", "sdu_password": "synthetic-password"},
        headers=HEADERS,
    )
    assert result.status_code == 503
    assert "synthetic-password" not in result.text
    async def stored():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(UserProfile))
            return row.sdu_login if row else None, row.sdu_password_encrypted if row else None

    assert asyncio.run(stored()) == (None, None)


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


def test_no_v1_route_crashes_on_an_unknown_session_token(client):
    """An expired session must always end in 401/403/404/422, never in a server error."""
    skipped = {"/v1/session/bootstrap", "/v1/telegram/webhook"}
    bad = {"Authorization": "Bearer unknown-token"}
    failures = []
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/v1") or path in skipped:
            continue
        concrete = path.replace("{homework_id}", str(uuid.uuid4()))
        concrete = concrete.replace("{deadline_id}", str(uuid.uuid4()))
        concrete = concrete.replace("{notification_id}", str(uuid.uuid4()))
        concrete = concrete.replace("{undo_id}", str(uuid.uuid4()))
        concrete = concrete.replace("{attachment_id}", str(uuid.uuid4()))
        concrete = concrete.replace("{lesson_id}", "x")
        if "{" in concrete:
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            response = client.request(
                method, concrete, headers=bad, json={} if method != "GET" else None
            )
            if response.status_code >= 500:
                failures.append((method, path, response.status_code))
    assert failures == []
