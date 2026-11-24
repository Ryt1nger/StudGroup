"""Urgent run requests are owner-only and deduplicate concurrent/repeated clicks."""

pytest_plugins = ["test_schedule_api"]


def test_urgent_run_request(client):
    url = "/v1/admin/sdo-runs"
    headers = {"Authorization": "Bearer valid"}
    assert client.post(url).status_code == 401
    assert client.post(url, headers=headers).status_code == 403
    client.app.state.settings.bot_admin_user_id = 1
    first = client.post(url, headers=headers)
    assert first.status_code == 202
    assert first.json()["state"] == "requested"
    second = client.post(url, headers=headers)
    assert second.json()["id"] == first.json()["id"]
    assert client.get(url, headers=headers).json()["run"] == first.json()
