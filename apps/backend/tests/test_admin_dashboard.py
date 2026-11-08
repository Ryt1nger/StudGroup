"""Admin telemetry is live, read-only and restricted to the configured bot operator."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import AcademicDeadline, AnalysisBudgetCall, RawMessage

pytest_plugins = ["test_schedule_api"]


def test_dashboard_rejects_students_and_missing_operator(client):
    assert client.get("/v1/admin/dashboard").status_code == 401
    assert (
        client.get("/v1/admin/dashboard", headers={"Authorization": "Bearer valid"}).status_code
        == 403
    )
    client.app.state.settings.bot_admin_user_id = 1
    assert (
        client.get("/v1/admin/dashboard", headers={"Authorization": "Bearer expired"}).status_code
        == 401
    )


def test_dashboard_reads_current_rows_and_filters_group(client):
    client.app.state.settings.bot_admin_user_id = 1

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            from studgroup.models import Group

            groups = (await db.scalars(select(Group))).all()
            selected = next(group for group in groups if group.pilot_authorized)
            other = next(group for group in groups if not group.pilot_authorized)
            selected_id = selected.id
            now = datetime.now(UTC)
            for group in (selected, other):
                db.add(
                    RawMessage(
                        group_id=group.id,
                        telegram_message_id=100 + int(group.pilot_authorized),
                        text="private",
                        message_date=now,
                        version_date=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
            db.add(
                AcademicDeadline(
                    group_id=selected.id,
                    import_key="test",
                    kind="exam",
                    subject="Math",
                    title="Exam",
                    description="private",
                    needs_clarification=True,
                    source_message_ids="[]",
                    created_at=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            db.add(
                AnalysisBudgetCall(
                    group_id=selected.id,
                    attempt_key="test",
                    kind="text",
                    state="settled",
                    reserved_usd=0.02,
                    charged_usd=0.01,
                    created_at=now,
                    settled_at=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            await db.commit()
            return selected_id

    group_id = asyncio.run(seed())
    response = client.get(
        f"/v1/admin/dashboard?group_id={group_id}", headers={"Authorization": "Bearer valid"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["messages_7d"] == 1
    assert body["summary"]["active_deadlines"] == 1
    assert body["summary"]["needs_clarification"] == 1
    assert body["summary"]["charged_usd"] == 0.01
    assert "private" not in response.text
    assert (
        client.get(
            "/v1/admin/dashboard?group_id=" + str(uuid4()),
            headers={"Authorization": "Bearer valid"},
        ).status_code
        == 404
    )


def test_admin_page_has_no_embedded_credentials(client):
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "initData" not in response.text
    assert "no-store" in response.headers["cache-control"]
