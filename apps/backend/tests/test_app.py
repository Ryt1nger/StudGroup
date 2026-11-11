import ssl
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from studgroup.main import Settings, create_app, database_engine_options


def test_health_and_readiness_failure():
    app = create_app(Settings())
    with TestClient(app) as client:
        original_engine = app.state.engine
        app.state.engine = Mock()
        app.state.engine.connect.side_effect = OSError("secret database url")
        app.state.redis.ping = AsyncMock(side_effect=OSError("secret redis url"))
        assert client.get("/health").json() == {
            "status": "ok",
            "ai_processing": "disabled",
        }
        response = client.get("/ready")
        assert response.status_code == 503
        assert "secret" not in response.text
        app.state.engine = original_engine


def test_health_reports_enabled_ai_without_exposing_configuration():
    with TestClient(create_app(Settings(ai_enabled=True))) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "ai_processing": "enabled",
        }


def test_cors_allows_only_frontend_origin():
    with TestClient(create_app(Settings())) as client:
        headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"}
        assert client.options("/health", headers=headers).status_code == 200
        headers["Origin"] = "https://untrusted.example"
        assert client.options("/health", headers=headers).status_code == 400


def test_embedded_mode_needs_no_redis_and_stops_on_shutdown(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db",
            processing_mode="embedded",
            ai_enabled=False,
            owner_telegram_user_id=None,
        )
    )
    with TestClient(app) as client:
        processor = app.state.processor
        app.state.redis.ping = AsyncMock(side_effect=AssertionError("Redis must not be called"))
        assert client.get("/ready").status_code == 200
        assert client.get("/ready").json()["checks"]["redis"] is None
    assert processor.cancelled()


def test_slow_optional_owner_diagnostics_does_not_block_startup(tmp_path, monkeypatch):
    import asyncio

    from studgroup import processing_diagnostics

    async def slow_diagnostics(_engine, _settings):
        await asyncio.Event().wait()

    monkeypatch.setattr(processing_diagnostics, "startup", slow_diagnostics)
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/nonblocking.db",
            owner_update_notifications_enabled=True,
            cabinet_schedule_background_enabled=False,
        )
    )
    with TestClient(app) as client:
        # Startup must finish even though the diagnostic query never finishes.
        assert client.get("/health").status_code == 200
        task = app.state.diagnostics_task
        assert task is not None
        assert not task.done()
    assert task.cancelled()


def test_render_postgres_urls_are_normalized_without_requiring_manual_driver_edits():
    assert (
        Settings(database_url="postgres://user:placeholder@host/db").database_url
        == "postgresql+asyncpg://user:placeholder@host/db"
    )


def test_database_project_guard_accepts_only_the_configured_supabase_project():
    import pytest

    ref = "ytghmfxjjyxkcaglbhfj"
    direct = f"postgresql://postgres:secret@db.{ref}.supabase.co:5432/postgres"
    pooler = f"postgresql://postgres.{ref}:secret@aws-0-us-west-1.pooler.supabase.com:5432/postgres"
    assert Settings(_env_file=None, database_url=direct, database_project_ref=ref)
    assert Settings(_env_file=None, database_url=pooler, database_project_ref=ref)
    with pytest.raises(ValueError, match="DATABASE_PROJECT_REF"):
        Settings(_env_file=None, database_url=direct, database_project_ref="oldproject")
    with pytest.raises(ValueError, match="DATABASE_PROJECT_REF"):
        Settings(
            _env_file=None,
            database_url="postgresql://user:pass@old-render-db/db",
            database_project_ref=ref,
        )


def test_database_tls_and_pool_are_opt_in():
    assert database_engine_options(Settings()) == {}
    ca = Path(__file__).parents[1] / "probes" / "prod-ca-2021.crt"
    settings = Settings(
        database_ca_file=str(ca),
        database_pool_size=3,
        database_max_overflow=0,
        database_pool_timeout=10,
    )
    options = database_engine_options(settings)
    assert options["pool_size"] == 3
    assert options["max_overflow"] == 0
    assert options["pool_timeout"] == 10
    assert options["connect_args"]["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert options["connect_args"]["ssl"].check_hostname


def test_supabase_session_pooler_defaults_to_one_bounded_connection():
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql+asyncpg://postgres.project:placeholder@"
            "aws-0-us-west-1.pooler.supabase.com:5432/postgres"
        ),
    )
    assert database_engine_options(settings) == {
        "pool_size": 1,
        "max_overflow": 0,
        "pool_timeout": 10,
    }


def test_explicit_supabase_pool_settings_override_safe_defaults():
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql+asyncpg://postgres.project:placeholder@"
            "aws-0-us-west-1.pooler.supabase.com:5432/postgres"
        ),
        database_pool_size=2,
        database_max_overflow=1,
        database_pool_timeout=20,
    )
    assert database_engine_options(settings) == {
        "pool_size": 2,
        "max_overflow": 1,
        "pool_timeout": 20,
    }


def test_web_api_pool_keeps_three_slots_if_render_config_disappears():
    # Session-pool default for standalone worker remains one, but public API
    # must not silently regress to the one-slot outage configuration.
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql+asyncpg://postgres.project:placeholder@"
            "aws-0-us-west-1.pooler.supabase.com:5432/postgres"
        ),
    )
    assert database_engine_options(settings, for_web_service=True) == {
        "pool_size": 3,
        "max_overflow": 0,
        "pool_timeout": 10,
    }
    assert database_engine_options(settings)["pool_size"] == 1


def test_web_api_pool_respects_explicit_five_connections():
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql+asyncpg://postgres.project:placeholder@"
            "aws-0-us-west-1.pooler.supabase.com:5432/postgres"
        ),
        database_pool_size=5,
        database_max_overflow=0,
        database_pool_timeout=10,
    )
    assert database_engine_options(settings, for_web_service=True) == {
        "pool_size": 5,
        "max_overflow": 0,
        "pool_timeout": 10,
    }


def test_database_tls_requires_postgres_and_existing_ca(tmp_path):
    import pytest

    with pytest.raises(ValueError, match="requires PostgreSQL"):
        database_engine_options(
            Settings(database_url="sqlite+aiosqlite:///test.db", database_ca_file="missing")
        )
    with pytest.raises(FileNotFoundError):
        database_engine_options(Settings(database_ca_file=str(tmp_path / "missing.crt")))


def test_cutover_read_only_skips_startup_tasks_and_rejects_writes(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/cutover.db",
            cutover_read_only=True,
            telegram_webhook_secret="test-secret",
        )
    )
    with TestClient(app) as client:
        assert app.state.redis is None
        assert app.state.processor is None
        assert app.state.cabinet_processor is None
        assert client.get("/ready").json() == {
            "status": "read_only",
            "checks": {"postgres": True, "redis": None},
        }
        for path in ("/v1/telegram/webhook", "/v1/session/bootstrap"):
            response = client.post(path, json={})
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "maintenance"
        assert client.get("/health").status_code == 200


def test_ai_cutoff_rejects_unspecified_timezone():
    import pytest

    with pytest.raises(ValueError):
        Settings(ai_enabled_until="2026-10-08T00:00:00")


def test_shadow_preparation_key_accepts_only_bounded_safe_identifiers():
    import pytest

    assert Settings(ai_shadow_prepare_once_key="canary-2").ai_shadow_prepare_once_key == (
        "canary-2"
    )
    assert Settings(ai_shadow_prepare_once_key="").ai_shadow_prepare_once_key is None
    assert Settings(ai_shadow_prepare_message_id="").ai_shadow_prepare_message_id is None
    assert Settings(ai_shadow_prepare_message_id="4029").ai_shadow_prepare_message_id == 4029
    with pytest.raises(ValueError):
        Settings(ai_shadow_prepare_message_id=0)
    assert Settings(ai_shadow_backfill_limit="25").ai_shadow_backfill_limit == 25
    assert Settings(ai_shadow_backfill_limit="").ai_shadow_backfill_limit is None
    for bad in (0, 201):
        with pytest.raises(ValueError):
            Settings(ai_shadow_backfill_limit=bad)
    with pytest.raises(ValueError):
        Settings(ai_shadow_backfill_key="WAVE/private")
    with pytest.raises(ValueError, match="invalid shadow preparation key"):
        Settings(ai_shadow_prepare_once_key="CANARY/private")
