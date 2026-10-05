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
