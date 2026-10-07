from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from studgroup.main import Settings, create_app


def test_health_and_readiness_failure():
    app = create_app(Settings())
    with TestClient(app) as client:
        original_engine = app.state.engine
        app.state.engine = Mock()
        app.state.engine.connect.side_effect = OSError("secret database url")
        app.state.redis.ping = AsyncMock(side_effect=OSError("secret redis url"))
        assert client.get("/health").json() == {"status": "ok"}
        response = client.get("/ready")
        assert response.status_code == 503
        assert "secret" not in response.text
        app.state.engine = original_engine


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


def test_render_postgres_urls_are_normalized_without_requiring_manual_driver_edits():
    assert (
        Settings(database_url="postgres://user:placeholder@host/db").database_url
        == "postgresql+asyncpg://user:placeholder@host/db"
    )


def test_ai_cutoff_rejects_unspecified_timezone():
    import pytest

    with pytest.raises(ValueError):
        Settings(ai_enabled_until="2026-10-08T00:00:00")
