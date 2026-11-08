"""The owner charts use real DB facts and do not leak message or AI content."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.admin_telemetry import RequestTelemetry
from studgroup.models import AnalysisBudgetCall, Group, RawMessage

pytest_plugins = ["test_schedule_api"]


def test_owner_visuals_authentication(client):
    assert client.get("/v1/admin/visuals").status_code == 401
    assert (
        client.get("/v1/admin/visuals", headers={"Authorization": "Bearer valid"}).status_code
        == 403
    )


def test_owner_visuals_real_charts_and_privacy(client):
    client.app.state.settings.bot_admin_user_id = 1

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            groups = (await db.scalars(select(Group))).all()
            selected = next(group for group in groups if group.pilot_authorized)
            other = next(group for group in groups if not group.pilot_authorized)
            now = datetime.now(UTC)
            for group, mid in ((selected, 9201), (other, 9202)):
                db.add(
                    RawMessage(
                        group_id=group.id,
                        telegram_message_id=mid,
                        sender_id=mid,
                        text="private message must not appear",
                        message_date=now,
                        version_date=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
            db.add(
                AnalysisBudgetCall(
                    group_id=selected.id,
                    attempt_key="budget-chart-test",
                    kind="photo",
                    state="settled",
                    reserved_usd=0.03,
                    charged_usd=0.019,
                    created_at=now,
                    settled_at=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            selected_id = selected.id
            await db.commit()
            return selected_id

    gid = asyncio.run(seed())
    response = client.get(
        f"/v1/admin/visuals?days=7&group_id={gid}",
        headers={"Authorization": "Bearer valid"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["daily"]) == 7
    assert sum(day["messages"] for day in data["daily"]) == 1
    assert len(data["by_hour_utc"]) == 24
    assert sum(item["value"] for item in data["by_hour_utc"]) == 1
    assert sum(row["usd"] for row in data["finance_month"]["by_kind"]) == 0.019
    assert sum(row["value"] for row in data["finance_month"]["series"]) == 0.019
    assert data["server"]["window_minutes"] == 15
    assert "private message must not appear" not in response.text
    assert (
        client.get(
            "/v1/admin/visuals?days=91", headers={"Authorization": "Bearer valid"}
        ).status_code
        == 422
    )


def test_http_monitor_uses_real_samples_only():
    monitor = RequestTelemetry()
    baseline = monitor.snapshot()
    assert baseline["sample_count"] == 0
    assert baseline["p95_ms"] is None
    monitor.record(200, 100)
    monitor.record(503, 500)
    result = monitor.snapshot()
    assert result["sample_count"] == 2
    assert result["errors_5xx"] == 1
    assert result["p50_ms"] == 100
    assert result["p95_ms"] == 100
    assert sum(item["count"] for item in result["requests"]) == 2
