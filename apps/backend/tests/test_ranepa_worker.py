"""Worker selects encrypted accounts by active group without moving secrets into jobs."""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup import ranepa_browser, ranepa_worker
from studgroup.legal import VERSION, document_hash
from studgroup.main import Settings
from studgroup.models import (
    Base,
    BotOutbox,
    BotPanel,
    Group,
    LegalConsentEvent,
    Membership,
    RanepaCrawlJob,
    User,
    UserProfile,
)
from studgroup.ranepa_crawl_notifications import CrawlRunStats
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot
from studgroup.ranepa_worker import (
    collect_bounded,
    create_worker_engine,
    prepare_week,
    process_one,
)


@pytest.mark.parametrize(
    ("proxy_url", "expected_proxy"),
    [
        (None, None),
        ("http://proxy.example.org:3128", {"server": "http://proxy.example.org:3128"}),
        (
            "http://service-user:private-password@proxy.example.org:3128",
            {
                "server": "http://proxy.example.org:3128",
                "username": "service-user",
                "password": "private-password",
            },
        ),
    ],
)
def test_collection_passes_proxy_to_browser_context(monkeypatch, proxy_url, expected_proxy):
    if proxy_url is None:
        monkeypatch.delenv("RANEPA_PROXY_URL", raising=False)
    else:
        monkeypatch.setenv("RANEPA_PROXY_URL", proxy_url)

    captured = {}

    class FakeContext:
        async def new_page(self):
            return object()

    class FakeBrowser:
        async def new_context(self, **kwargs):
            captured["context"] = kwargs
            return FakeContext()

        async def close(self):
            captured["closed"] = True

    class FakePlaywright:
        chromium = None

        async def __aenter__(self):
            self.chromium = self
            return self

        async def __aexit__(self, *args):
            return False

        async def launch(self, **kwargs):
            captured["launch"] = kwargs
            return FakeBrowser()

    async def captcha(*args):
        return SimpleNamespace(status="captcha_required")

    monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
    monkeypatch.setattr(ranepa_worker, "read_month", captcha)

    noticed = []

    async def on_captcha(location):
        noticed.append(location)

    status = asyncio.run(
        ranepa_worker._collect(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "student",
            "secret",
            date(2026, 11, 12),
            on_captcha=on_captcha,
        )
    )
    assert status == "captcha"
    assert noticed == ["month"]
    assert captured["launch"] == {"headless": True}
    assert captured["context"] == {
        "storage_state": None,
        "proxy": expected_proxy,
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
    }
    assert captured["closed"] is True


def test_restored_session_decrypts_and_corrupt_state_is_ignored():
    cipher = Fernet(Fernet.generate_key())
    state = {"cookies": [], "origins": []}
    encrypted = cipher.encrypt(json.dumps(state).encode()).decode()
    assert ranepa_worker.restored_storage_state(encrypted, cipher) == state
    assert ranepa_worker.restored_storage_state(None, cipher) is None
    assert ranepa_worker.restored_storage_state(encrypted, None) is None
    assert ranepa_worker.restored_storage_state("broken", cipher) is None
    assert ranepa_worker.restored_storage_state(encrypted, Fernet(Fernet.generate_key())) is None
    for payload in (b"not json", b"[]", b'{"cookies":{}}'):
        assert (
            ranepa_worker.restored_storage_state(cipher.encrypt(payload).decode(), cipher) is None
        )


@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("transient", ["none", "unreadable", "mismatch"])
def test_crawl_reuses_valid_session_or_logs_in_when_expired(monkeypatch, expired, transient):
    previous_state = {"cookies": [{"name": "lk", "value": "old"}], "origins": []}
    refreshed_state = {"cookies": [{"name": "lk", "value": "new"}], "origins": []}
    captured = {"contexts": [], "logins": [], "saved": [], "closed": False, "reads": {}}

    class FakePage:
        url = "about:blank"

        def __init__(self):
            self.logged_in = not expired

        async def goto(self, url, **kwargs):
            self.url = url

        async def wait_for_timeout(self, _milliseconds):
            return None

    class FakeContext:
        def __init__(self):
            self.page = FakePage()

        async def new_page(self):
            return self.page

        async def storage_state(self):
            return refreshed_state if expired else previous_state

    class FakeBrowser:
        async def new_context(self, **kwargs):
            captured["contexts"].append(kwargs)
            return FakeContext()

        async def close(self):
            captured["closed"] = True

    class FakePlaywright:
        async def __aenter__(self):
            self.chromium = self
            return self

        async def __aexit__(self, *args):
            return False

        async def launch(self, **kwargs):
            return FakeBrowser()

    async def visible_state(page):
        return "rendered" if page.logged_in else "login_required"

    async def sign_in(page, login, password):
        captured["logins"].append((login, password))
        page.logged_in = True
        return "rendered"

    async def month(page, login, password, code, year, month):
        status = await ranepa_browser._open(page, login, password)
        return SimpleNamespace(
            status="preview_only" if status == "rendered" else status,
            cards=(),
        )

    async def week(page, login, password, code, monday, *, allow_empty=False):
        assert allow_empty
        visits = captured["reads"].get(monday, 0) + 1
        captured["reads"][monday] = visits
        if monday == date(2026, 11, 9) and visits == 1:
            if transient == "unreadable":
                return SimpleNamespace(status="cabinet_day_mismatch", snapshot=None)
            if transient == "mismatch":
                extra = ExternalLesson(
                    day=monday + timedelta(days=6),
                    starts=time(9, 0),
                    ends=time(10, 30),
                    subject="Несогласованное занятие",
                )
                return SimpleNamespace(
                    status="preview_only",
                    snapshot=WeekSnapshot(code, monday, monday + timedelta(days=6), (extra,)),
                )
        return SimpleNamespace(
            status="preview_only",
            snapshot=WeekSnapshot(code, monday, monday + timedelta(days=6), ()),
        )

    async def sdo(*args, **kwargs):
        return {}, {}, {}

    async def remember(*args, **kwargs):
        assert kwargs["source_complete"] is True

    async def materialize(*args, **kwargs):
        pass

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def commit(self):
            pass

    async def persist(state):
        captured["saved"].append(state)

    monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
    monkeypatch.setattr(ranepa_browser, "_wait_visible_state", visible_state)
    monkeypatch.setattr(ranepa_browser, "_sign_in", sign_in)
    monkeypatch.setattr(ranepa_worker, "read_month", month)
    monkeypatch.setattr(ranepa_worker, "read_week", week)
    monkeypatch.setattr(ranepa_worker, "resolve_sdo_exits", sdo)
    monkeypatch.setattr(ranepa_worker, "remember_personal", remember)
    monkeypatch.setattr(ranepa_worker, "materialize_verified_facts", materialize)
    monkeypatch.setattr(ranepa_worker, "AsyncSession", lambda *_: FakeSession())
    monkeypatch.delenv("RANEPA_PROXY_URL", raising=False)

    from studgroup.ranepa_crawl_notifications import CrawlRunStats

    stats = CrawlRunStats()
    result = asyncio.run(
        ranepa_worker._collect(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "student",
            "password",
            date(2026, 11, 12),
            storage_state=previous_state,
            persist_session=persist,
            stats=stats,
        )
    )
    assert result is None
    assert stats.saved_weeks == 6
    assert stats.saved_lessons == 0
    assert stats.saved_sdo_links == 0
    assert captured["reads"][date(2026, 11, 9)] == (1 if transient == "none" else 2)
    assert captured["contexts"][0]["storage_state"] == previous_state
    assert captured["logins"] == ([("student", "password")] if expired else [])
    assert captured["saved"] == ([refreshed_state] if expired else [])
    assert captured["closed"] is True


@pytest.mark.parametrize(
    (
        "solver_succeeds",
        "expected_month_reads",
        "expected_status",
        "budget",
        "dry_run",
        "expected_solver_calls",
    ),
    [
        (False, 4, "captcha", 3, False, 3),
        (True, 2, "identity_unverified", 1, False, 1),
        (True, 1, "captcha", 0, False, 0),
        (False, 1, "captcha", 1, True, 1),
    ],
)
def test_worker_tries_at_most_three_captcha_solutions_and_checks_each(
    monkeypatch,
    solver_succeeds,
    expected_month_reads,
    expected_status,
    budget,
    dry_run,
    expected_solver_calls,
):
    observed = {"month": 0, "solve": 0, "diag_assets": 0, "post_drag_wait": 0, "close": False}

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return self

        async def new_page(self):
            return SimpleNamespace(url="https://my.ranepa.ru/lk/student/schedule")

        async def close(self):
            observed["close"] = True

    class FakePlaywright:
        async def __aenter__(self):
            self.chromium = self
            return self

        async def __aexit__(self, *args):
            return False

        async def launch(self, **kwargs):
            return FakeBrowser()

    async def read_captcha(*args):
        observed["month"] += 1
        status = (
            "identity_unverified"
            if solver_succeeds and not dry_run and observed["month"] == 2
            else "captcha_required"
        )
        return SimpleNamespace(status=status)

    async def solve(page, client, **kwargs):
        observed["solve"] += 1
        assert client is (None if dry_run else sentinel)
        assert kwargs["timeout_seconds"] == 65
        return solver_succeeds

    async def read_assets(_page):
        observed["diag_assets"] += 1
        return (b"test-left", b"test-right")

    async def wait_for_result(_page):
        observed["post_drag_wait"] += 1
        return "challenge_still_visible_after_wait"

    sentinel = object()
    monkeypatch.setattr(ranepa_worker, "_read_original_halves", read_assets)
    monkeypatch.setattr(ranepa_worker, "wait_for_captcha_resolution", wait_for_result)
    monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
    monkeypatch.setattr(ranepa_worker, "read_month", read_captcha)
    monkeypatch.setattr(ranepa_worker, "solve_slider_captcha", solve)
    monkeypatch.setattr(ranepa_worker, "solve_rotating_halves", solve)
    monkeypatch.delenv("RANEPA_PROXY_URL", raising=False)
    budget_tracker = ranepa_worker.CaptchaBudget(budget)
    status = asyncio.run(
        ranepa_worker._collect(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "student",
            "password",
            date(2026, 11, 12),
            captcha_client=sentinel,
            captcha_timeout_seconds=65,
            captcha_budget=budget_tracker,
            captcha_diagnostic_only=dry_run,
        )
    )
    assert status == expected_status
    assert observed["solve"] == expected_solver_calls
    assert observed["diag_assets"] == (1 if dry_run and budget else 0)
    assert observed["month"] == expected_month_reads
    assert observed["post_drag_wait"] == (1 if solver_succeeds and budget and not dry_run else 0)
    assert observed["close"] is True
    assert budget_tracker.remaining == (budget if dry_run else budget - expected_solver_calls)


def test_worker_engine_uses_bounded_supabase_pool(monkeypatch):
    captured = {}

    def fake_engine(url, **kwargs):
        captured.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr("studgroup.ranepa_worker.create_async_engine", fake_engine)
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql+asyncpg://postgres.project:placeholder@"
            "aws-0-us-west-1.pooler.supabase.com:5432/postgres"
        ),
    )
    create_worker_engine(settings)
    assert captured["pool_size"] == 1
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 10
    assert captured["hide_parameters"] is True


def test_account_collection_has_a_hard_runtime_limit(monkeypatch):
    async def never_finishes(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("studgroup.ranepa_worker._collect", never_finishes)
    monkeypatch.setattr("studgroup.ranepa_worker.ACCOUNT_TIMEOUT_SECONDS", 0.001)
    result = asyncio.run(
        collect_bounded(None, uuid4(), uuid4(), "A-001", "login", "password", date(2026, 11, 11))
    )
    assert result == "source_unavailable"


def test_worker_claims_only_connected_members_and_keeps_credentials_out_of_queue(
    tmp_path, monkeypatch
):
    async def scenario():
        path = tmp_path / "worker.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        key = Fernet.generate_key()
        credential_id = uuid4()
        encrypted = Fernet(key).encrypt(b"password").decode()

        class FakeStore:
            async def get(self, item_id, *, include_session_state=False):
                assert item_id == credential_id and include_session_state
                return ("login@ranepa.ru", encrypted, "v1", None)

        monkeypatch.setattr("studgroup.ranepa_worker.configured_store", lambda _: FakeStore())
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{path}",
            profile_encryption_key=SecretStr(key.decode()),
            legal_operator_name="Test operator",
            legal_operator_address="Test address",
            legal_operator_contact="test@example.test",
            legal_data_region="Moscow",
            legal_documents_approved=True,
            ranepa_access_approved=True,
        )
        assert settings.ranepa_session_reuse_enabled is False
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-98001,
                    name="A",
                    academic_group_code="A-001",
                    timezone="Europe/Moscow",
                )
                user = User(id=uuid4(), telegram_user_id=6001, display_name="Student")
                other = User(id=uuid4(), telegram_user_id=6002, display_name="Other")
                db.add_all([group, user, other])
                await db.flush()
                db.add_all(
                    [
                        Membership(
                            group_id=group.id, user_id=user.id, role="student", status="active"
                        ),
                        Membership(
                            group_id=group.id, user_id=other.id, role="student", status="active"
                        ),
                        UserProfile(
                            user_id=user.id,
                            sdu_credential_id=credential_id,
                            updated_at=datetime.now(UTC),
                        ),
                        LegalConsentEvent(
                            user_id=user.id,
                            purpose="sdo",
                            action="accept",
                            document_version=VERSION,
                            document_sha256=document_hash("sdo", settings),
                            source="web",
                            created_at=datetime.now(UTC),
                        ),
                    ]
                )
                await db.commit()
            settings.ranepa_access_approved = False
            assert await prepare_week(engine, date(2026, 11, 6), settings) == 0
            settings.ranepa_access_approved = True
            assert await prepare_week(engine, date(2026, 11, 6), settings) == 1
            assert await prepare_week(engine, date(2026, 11, 6), settings) == 0
            async with AsyncSession(engine) as db:
                jobs = (await db.scalars(select(RanepaCrawlJob))).all()
                assert len(jobs) == 1 and jobs[0].user_id == user.id
                assert "password" not in str(jobs[0].__dict__)
            seen = []

            async def fake_collect(
                engine_, group_id, user_id, code, login, password, today, **kwargs
            ):
                assert kwargs["storage_state"] is None
                assert kwargs["persist_session"] is None
                seen.append((group_id, user_id, code, login, password))

            monkeypatch.setattr("studgroup.ranepa_worker._collect", fake_collect)
            assert await process_one(engine, settings, date(2026, 11, 6)) == "succeeded"
            assert seen == [(group.id, user.id, "A-001", "login", "password")]
            # Revoking consent excludes this account from the next weekly round.
            async with AsyncSession(engine) as db:
                db.add(
                    LegalConsentEvent(
                        user_id=user.id,
                        purpose="sdo",
                        action="revoke",
                        document_version=VERSION,
                        document_sha256=document_hash("sdo", settings),
                        source="web",
                        created_at=datetime.now(UTC),
                    )
                )
                await db.commit()
            assert await prepare_week(engine, date(2026, 11, 9), settings) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_manual_pilot_prepares_and_runs_only_selected_student(tmp_path, monkeypatch):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/targeted.db")
        key = Fernet.generate_key()
        encrypted = Fernet(key).encrypt(b"test-password").decode()
        settings = Settings(
            _env_file=None,
            database_url=f"sqlite+aiosqlite:///{tmp_path}/targeted.db",
            profile_encryption_key=SecretStr(key.decode()),
            legal_operator_name="Test operator",
            legal_operator_address="Test address",
            legal_operator_contact="test@example.test",
            legal_data_region="Moscow",
            legal_documents_approved=True,
            ranepa_access_approved=True,
            ranepa_session_reuse_enabled=True,
            ranepa_captcha_enabled=True,
            captcha_api_key=SecretStr("test-provider-key"),
            ranepa_captcha_diagnostic_only=True,
            ranepa_crawl_notify_account_results_enabled=True,
            bot_admin_user_id=915,
        )

        saved_state = {"cookies": [], "origins": []}
        new_state = {"cookies": [{"name": "test", "value": "fresh"}], "origins": []}
        encrypted_state = Fernet(key).encrypt(json.dumps(saved_state).encode()).decode()
        saved_updates = []

        class FakeStore:
            async def get(self, _key, *, include_session_state=False):
                assert include_session_state
                return ("student@example.edu", encrypted, "v1", encrypted_state)

            async def update_session_state(self, _key, value, **kwargs):
                saved_updates.append((value, kwargs))

        monkeypatch.setattr("studgroup.ranepa_worker.configured_store", lambda _: FakeStore())
        seen = []

        async def fake_collect(_engine, group_id, user_id, code, login, password, _today, **kwargs):
            seen.append((group_id, user_id, code, login, password))
            assert kwargs["storage_state"] == saved_state
            assert kwargs["captcha_client"] is None
            assert kwargs["captcha_diagnostic_only"] is True
            await kwargs["persist_session"](new_state)

        monkeypatch.setattr("studgroup.ranepa_worker._collect", fake_collect)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            group = Group(
                id=uuid4(),
                telegram_chat_id=-97401,
                name="Pilot",
                academic_group_code="P-001",
                timezone="Europe/Moscow",
            )
            users = [
                User(id=uuid4(), telegram_user_id=97401 + i, display_name=f"U{i}") for i in range(2)
            ]
            async with AsyncSession(engine, expire_on_commit=False) as db:
                db.add_all([group, *users])
                await db.flush()
                for user in users:
                    db.add(
                        Membership(
                            group_id=group.id, user_id=user.id, role="student", status="active"
                        )
                    )
                    db.add(
                        UserProfile(
                            user_id=user.id,
                            sdu_credential_id=uuid4(),
                            updated_at=datetime.now(UTC),
                        )
                    )
                    db.add(
                        LegalConsentEvent(
                            user_id=user.id,
                            purpose="sdo",
                            action="accept",
                            document_version=VERSION,
                            document_sha256=document_hash("sdo", settings),
                            source="web",
                            created_at=datetime.now(UTC),
                        )
                    )
                await db.commit()
            today = date(2026, 11, 11)
            assert (
                await prepare_week(
                    engine,
                    today,
                    settings,
                    pilot_group_id=group.id,
                    pilot_user_id=users[1].id,
                )
                == 1
            )
            run_stats = CrawlRunStats()
            assert (
                await process_one(
                    engine,
                    settings,
                    today,
                    pilot_group_id=group.id,
                    pilot_user_id=users[1].id,
                    run_stats=run_stats,
                )
                == "succeeded"
            )
            assert seen == [(group.id, users[1].id, "P-001", "student", "test-password")]
            assert len(saved_updates) == 1
            encrypted_payload, conditions = saved_updates[0]
            assert json.loads(Fernet(key).decrypt(encrypted_payload.encode())) == new_state
            assert "fresh" not in encrypted_payload
            assert conditions == {
                "login": "student@example.edu",
                "ciphertext": encrypted,
                "key_version": "v1",
            }
            async with AsyncSession(engine) as db:
                jobs = (await db.scalars(select(RanepaCrawlJob))).all()
                assert len(jobs) == 1
                assert jobs[0].user_id == users[1].id and jobs[0].state == "succeeded"
                notices = (await db.scalars(select(BotOutbox))).all()
                assert len(notices) == 1
                notice = json.loads(notices[0].payload)
                assert notice["chat_id"] == 915
                assert "U1" in notice["text"]
                assert "Группа РАНХиГС: P-001" in notice["text"]
                assert notices[0].dedup_key.startswith("ranepa-crawl-account:")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cooldown_bypass_requires_explicit_group_and_user_ids():
    async def scenario():
        with pytest.raises(ValueError, match="cooldown_bypass_requires_targeted_pilot"):
            await prepare_week(
                None,
                date(2026, 11, 13),
                Settings(_env_file=None),
                bypass_cooldown=True,
            )

    asyncio.run(scenario())


def test_single_pilot_explicitly_requests_only_its_own_cooldown_bypass(monkeypatch):
    async def scenario():
        target_group, target_user = uuid4(), uuid4()
        calls = []

        class FakeEngine:
            async def dispose(self):
                calls.append("disposed")

        async def prepare(engine, today, settings, **kwargs):
            calls.append(("prepare", kwargs))

        async def process(engine, settings, today, **kwargs):
            calls.append(("process", kwargs))
            return "succeeded"

        monkeypatch.setattr(ranepa_worker, "legal_ready", lambda settings: True)
        monkeypatch.setattr(ranepa_worker, "configured_store", lambda settings: object())
        monkeypatch.setattr(ranepa_worker, "create_worker_engine", lambda settings: FakeEngine())
        monkeypatch.setattr(ranepa_worker, "prepare_week", prepare)
        monkeypatch.setattr(ranepa_worker, "process_one", process)
        settings = Settings(
            _env_file=None,
            ranepa_crawl_enabled=True,
            ranepa_access_approved=True,
            profile_encryption_key=SecretStr("placeholder-key"),
            database_url="postgresql+asyncpg://localhost/test",
        )

        assert (
            await ranepa_worker.run_single_pilot(
                settings, group_id=target_group, user_id=target_user
            )
            == "succeeded"
        )
        assert calls[0][0] == "prepare"
        assert calls[0][1] == {
            "pilot_group_id": target_group,
            "pilot_user_id": target_user,
            "bypass_cooldown": True,
        }
        assert calls[1][0] == "process"
        assert calls[1][1]["pilot_group_id"] == target_group
        assert calls[1][1]["pilot_user_id"] == target_user
        assert calls[2] == "disposed"

    asyncio.run(scenario())


def test_worker_preserves_week_and_flags_unresolved_sdo_captcha(monkeypatch):
    from studgroup.ranepa_crawl_notifications import CrawlRunStats
    from studgroup.ranepa_crawl_trace import CrawlTrace

    recorded = []

    class FakeContext:
        async def new_page(self):
            return object()

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return FakeContext()

        async def close(self):
            return None

    class FakePlaywright:
        async def __aenter__(self):
            self.chromium = self
            return self

        async def __aexit__(self, *args):
            return False

        async def launch(self, **kwargs):
            return FakeBrowser()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def commit(self):
            recorded.append("committed")

    async def month(*args):
        return SimpleNamespace(status="preview_only", cards=())

    async def week(_page, _login, _password, code, monday, *, allow_empty=False):
        assert allow_empty
        return SimpleNamespace(
            status="preview_only",
            snapshot=WeekSnapshot(code, monday, monday + timedelta(days=6), ()),
        )

    async def exits(*args, **kwargs):
        return {}, {"test-sdo-transition": "captcha_required"}, {}

    async def save(*args, **kwargs):
        assert kwargs["source_complete"] is True
        recorded.append("saved")

    async def materialize(*args, **kwargs):
        recorded.append("materialized")

    monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
    monkeypatch.setattr(ranepa_worker, "read_month", month)
    monkeypatch.setattr(ranepa_worker, "read_week", week)
    monkeypatch.setattr(ranepa_worker, "resolve_sdo_exits", exits)
    monkeypatch.setattr(ranepa_worker, "remember_personal", save)
    monkeypatch.setattr(ranepa_worker, "materialize_verified_facts", materialize)
    monkeypatch.setattr(ranepa_worker, "AsyncSession", lambda *_: FakeSession())
    trace = CrawlTrace()
    stats = CrawlRunStats()

    result = asyncio.run(
        ranepa_worker._collect(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "student",
            "password",
            date(2026, 11, 12),
            trace=trace,
            stats=stats,
        )
    )
    assert result == "captcha"
    assert stats.saved_weeks == 1
    assert stats.saved_lessons == 0
    assert recorded == ["saved", "materialized", "committed"]
    assert trace.captcha_seen
    assert trace.captcha_location == "sdo"
    assert trace.preceding_step == "sdo_exits_requested"


def test_owner_discovery_is_private_unique_and_fail_closed(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/owner-lookup.db")
        settings = Settings(
            _env_file=None,
            bot_admin_user_id=None,
            ranepa_captcha_guard_enabled=True,
            ranepa_crawl_notify_account_results_enabled=True,
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            with pytest.raises(RuntimeError, match="ranepa_unique_owner_recipient_required"):
                await ranepa_worker.ensure_owner_for_crawl(engine, settings)

            async with AsyncSession(engine) as db:
                owner = User(id=uuid4(), telegram_user_id=95001, display_name="Owner")
                db.add_all([owner, BotPanel(chat_id=95001, mode="admin")])
                await db.commit()
            await ranepa_worker.ensure_owner_for_crawl(engine, settings)
            assert settings.bot_admin_user_id == 95001

            settings.bot_admin_user_id = None
            async with AsyncSession(engine) as db:
                other = User(id=uuid4(), telegram_user_id=95002, display_name="Other")
                db.add_all([other, BotPanel(chat_id=95002, mode="admin")])
                await db.commit()
            with pytest.raises(RuntimeError, match="ranepa_unique_owner_recipient_required"):
                await ranepa_worker.ensure_owner_for_crawl(engine, settings)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_configured_owner_avoids_lookup_when_authorized():
    settings = Settings(
        _env_file=None,
        bot_admin_user_id=95003,
        ranepa_captcha_guard_enabled=True,
    )
    asyncio.run(ranepa_worker.ensure_owner_for_crawl(None, settings))
    assert settings.bot_admin_user_id == 95003


@pytest.mark.parametrize(
    ("provider_submitted", "paid_tasks", "expected_attempts"),
    [(True, 6, 3), (False, 6, 3), (False, 4, 2)],
)
def test_three_rucaptcha_attempts_precede_owner_fallback(
    monkeypatch, provider_submitted, paid_tasks, expected_attempts
):
    calls = []
    page = SimpleNamespace(url="https://my.ranepa.ru/captcha")

    class FakeContext:
        async def new_page(self):
            return page

    class FakeBrowser:
        async def new_context(self, **_kwargs):
            return FakeContext()

        async def close(self):
            calls.append("browser_closed")

    class FakePlaywright:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        @property
        def chromium(self):
            return self

        async def launch(self, **_kwargs):
            return FakeBrowser()

    reads = 0

    async def read_month(*args):
        nonlocal reads
        reads += 1
        return SimpleNamespace(
            status="preview_only" if "manual" in calls else "captcha_required",
            cards=[],
        )

    async def read_week(*args, **kwargs):
        return SimpleNamespace(status="identity_unverified", snapshot=None)

    async def auto(*args, **kwargs):
        assert "manual" not in calls
        calls.append("rucaptcha")
        return provider_submitted

    async def manual(page_argument, auto_attempts):
        assert page_argument is page
        assert auto_attempts == expected_attempts
        calls.append("manual")
        return True

    async def waited(*args, **kwargs):
        return "challenge_still_visible_after_wait"

    monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
    monkeypatch.setattr(ranepa_worker, "read_month", read_month)
    monkeypatch.setattr(ranepa_worker, "read_week", read_week)
    monkeypatch.setattr(ranepa_worker, "solve_rotating_halves", auto)
    monkeypatch.setattr(ranepa_worker, "wait_for_captcha_resolution", waited)
    client = SimpleNamespace(max_tasks=2)
    result = asyncio.run(
        ranepa_worker._collect(
            None,
            uuid4(),
            uuid4(),
            "TEST-001",
            "test-login",
            "test-password",
            date(2026, 11, 15),
            captcha_client=client,
            captcha_budget=ranepa_worker.CaptchaBudget(paid_tasks),
            manual_captcha=manual,
        )
    )
    assert result == "identity_unverified"
    assert calls == ["rucaptcha"] * expected_attempts + ["manual", "browser_closed"]
    assert reads == expected_attempts + 2


@pytest.mark.parametrize("state", ["ambiguous", "identity_missing", "captcha_required"])
def test_worker_persists_verified_schedule_without_mocking_writer(tmp_path, monkeypatch, state):
    """The browser is fake, but the SQLAlchemy snapshot writer is real."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from studgroup.models import Base, Group, Membership, RanepaPersonalSnapshot, User

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/writer-{state}.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            group_id, user_id = uuid4(), uuid4()
            async with AsyncSession(engine) as db:
                db.add(
                    Group(
                        id=group_id,
                        name="Test",
                        telegram_chat_id=-90011,
                        timezone="Europe/Moscow",
                        academic_group_code="A-001",
                    )
                )
                db.add(User(id=user_id, telegram_user_id=991101, display_name="Test"))
                db.add(
                    Membership(
                        id=uuid4(),
                        group_id=group_id,
                        user_id=user_id,
                        status="active",
                        role="student",
                    )
                )
                await db.commit()

            class FakeContext:
                async def new_page(self):
                    return SimpleNamespace()

            class FakeBrowser:
                async def new_context(self, **kwargs):
                    return FakeContext()

                async def close(self):
                    return None

            class FakePlaywright:
                async def __aenter__(self):
                    self.chromium = self
                    return self

                async def __aexit__(self, *args):
                    return False

                async def launch(self, **kwargs):
                    return FakeBrowser()

            monday = date(2026, 11, 16)
            cards = [
                SimpleNamespace(
                    day=monday + timedelta(weeks=i),
                    starts=time(9),
                    subject="История",
                )
                for i in range(6)
            ]

            async def month(_page, _login, _password, _code, year, number):
                return SimpleNamespace(
                    status="preview_only",
                    cards=[
                        card for card in cards if (card.day.year, card.day.month) == (year, number)
                    ],
                )

            async def read(_page, _login, _password, code, start, *, allow_empty=False):
                lesson = ExternalLesson(
                    day=start,
                    starts=time(9),
                    ends=time(10, 20),
                    subject="История",
                    teacher="Иванов",
                    room="СДО",
                )
                return SimpleNamespace(
                    status="preview_only",
                    snapshot=WeekSnapshot(code, start, start + timedelta(days=6), (lesson,)),
                )

            async def exits(_context, result, *_args, **_kwargs):
                lesson = result.snapshot.lessons[0]
                key = (
                    lesson.day.isoformat(),
                    lesson.starts.isoformat(),
                    lesson.ends.isoformat(),
                    lesson.subject,
                    lesson.teacher,
                    lesson.room,
                )
                return {}, {key: state}, {}

            monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
            monkeypatch.setattr(ranepa_worker, "read_month", month)
            monkeypatch.setattr(ranepa_worker, "read_week", read)
            monkeypatch.setattr(ranepa_worker, "resolve_sdo_exits", exits)
            outcome = await ranepa_worker._collect(
                engine,
                group_id,
                user_id,
                "A-001",
                "test",
                "test",
                date(2026, 11, 16),
            )
            assert outcome == ("captcha" if state == "captcha_required" else None)
            async with AsyncSession(engine) as db:
                stored = await db.get(RanepaPersonalSnapshot, (group_id, user_id, monday))
                assert stored is not None and stored.identity_verified is True
                import json

                rows = json.loads(stored.rows_json)
                assert len(rows) == 1
                assert rows[0]["link_state"] == state
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_owner_notification_name_prefers_profile_when_telegram_name_is_placeholder():
    user = SimpleNamespace(display_name="-", username="student_handle")
    profile = SimpleNamespace(first_name="Иван", last_name="Иванов")
    assert ranepa_worker.owner_student_name(user, profile) == "Иванов Иван"
    assert ranepa_worker.owner_student_name(user, None) == "@student_handle"
    assert (
        ranepa_worker.owner_student_name(
            SimpleNamespace(display_name="Katerina", username=None), profile
        )
        == "Katerina"
    )
    assert ranepa_worker.owner_student_name(None, None) == "Имя не указано"


def test_balanced_sample_alternates_groups_and_caps_distinct_due_users():
    group_ids = [UUID(int=i) for i in range(1, 5)]
    users = [UUID(int=100 + i) for i in range(18)]
    queued = (
        [(group_ids[0], user) for user in users[:10]]
        + [(group_ids[1], user) for user in users[10:13]]
        + [(group_ids[2], user) for user in users[13:17]]
        + [(group_ids[3], users[17])]
    )
    ordered = ranepa_worker.balanced_sample_users(queued, limit=15)
    assert len(ordered) == len(set(ordered)) == 15
    assert ordered[:4] == (users[0], users[10], users[13], users[17])
    assert users[17] in ordered
    assert ranepa_worker.balanced_sample_users(queued[:1], limit=15) == (users[0],)
    with pytest.raises(ValueError, match="invalid_sdo_sample_limit"):
        ranepa_worker.balanced_sample_users(queued, limit=16)
    with pytest.raises(ValueError, match="invalid_sdo_sample_limit"):
        ranepa_worker.balanced_sample_users(queued, limit=0)


def test_failed_month_recheck_after_captcha_is_not_called_site_outage(monkeypatch):
    from studgroup.ranepa_crawl_trace import CrawlTrace

    calls = 0

    class FakeContext:
        async def new_page(self):
            return SimpleNamespace(url="https://my.ranepa.ru/lk/student/schedule")

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return FakeContext()

        async def close(self):
            return None

    class FakePlaywright:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        @property
        def chromium(self):
            return self

        async def launch(self, **kwargs):
            return FakeBrowser()

    async def month(*args):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            status="captcha_required" if calls == 1 else "source_unavailable",
            diagnostic=None,
        )

    async def submitted(*args, **kwargs):
        return True

    async def wait(*args):
        return "challenge_still_visible_after_wait"

    monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
    monkeypatch.setattr(ranepa_worker, "read_month", month)
    monkeypatch.setattr(ranepa_worker, "solve_rotating_halves", submitted)
    monkeypatch.setattr(ranepa_worker, "wait_for_captcha_resolution", wait)
    trace = CrawlTrace()
    status = asyncio.run(
        ranepa_worker._collect(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "login",
            "password",
            date(2026, 11, 20),
            captcha_client=SimpleNamespace(max_tasks=1),
            trace=trace,
        )
    )
    assert calls == 2
    assert status == "captcha"
    assert trace.failure_detail == "captcha_unverified"
    assert trace.diagnostic == "challenge_pending"


@pytest.mark.parametrize("future_month_available", [True, False])
def test_bounded_optional_sdo_discovery_never_invalidates_six_committed_weeks(
    monkeypatch, future_month_available
):
    from studgroup.ranepa_crawl_trace import CrawlTrace

    saved = []
    months = []

    class FakeContext:
        async def new_page(self):
            return object()

    class FakeBrowser:
        async def new_context(self, **kwargs):
            return FakeContext()

        async def close(self):
            return None

    class FakePlaywright:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        @property
        def chromium(self):
            return self

        async def launch(self, **kwargs):
            return FakeBrowser()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def commit(self):
            return None

    async def month(_page, _login, _password, _code, year, number):
        months.append((year, number))
        if number == 1 and not future_month_available:
            return SimpleNamespace(status="empty_source_requires_review")
        cards = (
            SimpleNamespace(day=date(2026, 12, 1), starts=time(9), subject="История"),
            SimpleNamespace(day=date(2027, 1, 1), starts=time(9), subject="История"),
        )
        return SimpleNamespace(
            status="preview_only",
            cards=[card for card in cards if (card.day.year, card.day.month) == (year, number)],
        )

    async def week(_page, _login, _password, code, monday, *, allow_empty=False):
        end = monday + timedelta(days=6)
        lessons = tuple(
            ExternalLesson(day=day, starts=time(9), ends=time(10), subject="История")
            for day in (date(2026, 12, 1), date(2027, 1, 1))
            if monday <= day <= end
        )
        assert allow_empty == (not lessons)
        return SimpleNamespace(
            status="preview_only",
            snapshot=WeekSnapshot(code, monday, end, lessons),
        )

    async def exits(*args, **kwargs):
        await asyncio.Event().wait()  # Never finishes; budget MUST cancel it.

    async def save(db, group_id, user_id, snapshot, **kwargs):
        saved.append(snapshot.week_start)

    async def materialize(*args, **kwargs):
        return None

    monkeypatch.setattr(ranepa_worker, "async_playwright", FakePlaywright)
    monkeypatch.setattr(ranepa_worker, "read_month", month)
    monkeypatch.setattr(ranepa_worker, "read_week", week)
    monkeypatch.setattr(ranepa_worker, "resolve_sdo_exits", exits)
    monkeypatch.setattr(ranepa_worker, "remember_personal", save)
    monkeypatch.setattr(ranepa_worker, "materialize_verified_facts", materialize)
    monkeypatch.setattr(ranepa_worker, "AsyncSession", lambda *_: FakeSession())
    monkeypatch.setattr(ranepa_worker, "MAX_SDO_SCAN_WEEK_SECONDS", 0.01)
    monkeypatch.setattr(ranepa_worker, "MAX_SDO_SCAN_TOTAL_SECONDS", 0.02)
    stats = CrawlRunStats()
    trace = CrawlTrace()
    outcome = asyncio.run(
        ranepa_worker._collect(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "login",
            "password",
            date(2026, 11, 24),
            trace=trace,
            stats=stats,
        )
    )
    assert months == [(2026, 11), (2026, 12), (2027, 1)]
    expected_count = 6 if future_month_available else 5
    assert outcome == (None if future_month_available else "parse_failed")
    assert stats.saved_weeks == expected_count
    assert saved == [date(2026, 11, 23) + timedelta(weeks=i) for i in range(expected_count)]
    if future_month_available:
        assert trace.events[-1]["step"] == "schedule_verified"
    assert any(e["step"] == "sdo_exits_timed_out" for e in trace.events)


def test_account_timeout_preserves_complete_snapshots(monkeypatch):
    from studgroup.ranepa_crawl_trace import CrawlTrace

    async def timed_out(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(ranepa_worker, "_collect", timed_out)
    trace = CrawlTrace()
    stats = CrawlRunStats(saved_weeks=6)
    result = asyncio.run(
        collect_bounded(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "login",
            "password",
            date(2026, 11, 20),
            trace=trace,
            stats=stats,
        )
    )
    assert result is None
    assert trace.diagnostic == "timeout"
    assert trace.events[-1]["step"] == "schedule_verified"

    incomplete = CrawlRunStats(saved_weeks=5)
    result = asyncio.run(
        collect_bounded(
            None,
            uuid4(),
            uuid4(),
            "A-001",
            "login",
            "password",
            date(2026, 11, 20),
            stats=incomplete,
        )
    )
    assert result == "source_unavailable"
