"""Staff-facing RANEPA source setup; preview must never change student calendars."""

from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from test_staff_api import AUTH, make

from studgroup.ranepa_probe import PublicProbe

pytest_plugins = ["test_schedule_api"]
CODE = "Э-ОБ-385-04-26-02"
UUID_GROUP = UUID("00000000-0000-4000-8000-000000000001")


def week():
    day = datetime.now(ZoneInfo("Europe/Moscow")).date()
    return day - timedelta(days=day.weekday())


def test_only_headman_can_configure_source(client):
    url = "/v1/staff/ranepa-schedule"
    assert client.get(url, headers=AUTH).status_code == 403
    make(client, "deputy")
    assert client.get(url, headers=AUTH).json()["configured"] is False
    blocked = client.put(url, json={"official_group_code": CODE}, headers=AUTH)
    assert blocked.status_code == 403
    make(client, "headman")
    reply = client.put(url, json={"official_group_code": CODE}, headers=AUTH)
    assert reply.status_code == 200, reply.text
    assert reply.json() == {
        "configured": True,
        "group_code": CODE,
        "sync_enabled": False,
    }
    assert client.get(url, headers=AUTH).json()["group_code"] == CODE


def test_preview_only_when_site_identifies_correct_group(client, monkeypatch):
    import studgroup.ranepa_schedule_api as api

    make(client, "headman")
    url = "/v1/staff/ranepa-schedule"
    assert client.put(url, json={"official_group_code": CODE}, headers=AUTH).status_code == 200
    monday = week()

    async def validated(internal_uuid, code):
        assert internal_uuid == UUID_GROUP and code == CODE
        day = f"{monday.month}/{monday.day}/{monday.year}"
        html = (
            "<html><h1>"
            + CODE
            + "</h1><h2>"
            + day
            + " Понедельник</h2>"
            + "<div>09:00 - 10:20</div><div>305</div>"
            + "<div>Иностранный язык</div>"
            + "<div>Практические занятия</div><div>Иванова</div><div>"
            + CODE
            + "</div></html>"
        )
        return PublicProbe(CODE, str(internal_uuid), 1, "f" * 64), html

    monkeypatch.setattr(api, "fetch_public_page", validated)
    preview = client.post(
        url + "/preview",
        headers=AUTH,
        json={"internal_group_uuid": str(UUID_GROUP), "week_start": monday.isoformat()},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["complete"] is False
    assert preview.json()["lessons"][0]["subject"] == "Иностранный язык"
    assert preview.json()["status"] == "preview_only"
    assert client.get(url, headers=AUTH).json()["internal_group_uuid"] is None


def test_preview_fail_closed_on_wrong_group_or_week(client):
    make(client, "headman")
    url = "/v1/staff/ranepa-schedule"
    assert client.put(url, json={"official_group_code": CODE}, headers=AUTH).status_code == 200
    monday = week()
    wrong_day = monday + timedelta(days=1)
    bad = client.post(
        url + "/preview",
        headers=AUTH,
        json={"internal_group_uuid": str(UUID_GROUP), "week_start": wrong_day.isoformat()},
    )
    assert bad.status_code == 422
