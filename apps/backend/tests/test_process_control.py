"""Desired process state is durable, restricted and not a startup acknowledgement."""

pytest_plugins = ["test_schedule_api"]

OWNER = {"Authorization": "Bearer valid"}
ROOT = "/v1/admin/processes"


def test_process_controls_owner_only(client):
    assert client.get(ROOT).status_code == 401
    assert client.post(f"{ROOT}/sdo/enable", headers=OWNER).status_code == 403
    client.app.state.settings.bot_admin_user_id = 1
    assert client.get(ROOT, headers=OWNER).status_code == 200
    assert client.post(f"{ROOT}/shell/enable", headers=OWNER).status_code == 422
    assert client.post(f"{ROOT}/sdo/exec", headers=OWNER).status_code == 422


def test_process_request_persists_without_claiming_runtime_success(client):
    client.app.state.settings.bot_admin_user_id = 1
    first = client.post(f"{ROOT}/sdo/enable", headers=OWNER)
    assert first.status_code == 202
    body = first.json()
    assert body["enabled"] is True
    assert body["pending"] is True
    assert body["status"] == "unknown"
    assert body["heartbeat_at"] is None
    generation = body["generation"]
    again = client.post(f"{ROOT}/sdo/enable", headers=OWNER).json()
    assert again["generation"] == generation
    restart = client.post(f"{ROOT}/sdo/restart", headers=OWNER).json()
    assert restart["generation"] == generation + 1
    disabled = client.post(f"{ROOT}/sdo/disable", headers=OWNER).json()
    assert disabled["enabled"] is False
    assert disabled["generation"] == generation + 2
    rows = client.get(ROOT, headers=OWNER).json()["processes"]
    assert next(row for row in rows if row["name"] == "sdo") == disabled
