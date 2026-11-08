"""Owner analytics must stay real, group-scoped, paginated and confidential."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import AnalysisBudgetCall, Group, RawMessage

pytest_plugins = ["test_schedule_api"]


def test_analytics_denies_non_owners(client):
    assert client.get("/v1/admin/analytics").status_code == 401
    assert client.get("/v1/admin/details/errors").status_code == 401
    assert (
        client.get("/v1/admin/analytics", headers={"Authorization": "Bearer valid"}).status_code
        == 403
    )
    client.app.state.settings.bot_admin_user_id = 1
    assert (
        client.get("/v1/admin/analytics", headers={"Authorization": "Bearer expired"}).status_code
        == 401
    )


def test_analytics_real_numbers_and_drilldown(client):
    client.app.state.settings.bot_admin_user_id = 1

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            groups = (await db.scalars(select(Group))).all()
            chosen = next(group for group in groups if group.pilot_authorized)
            other = next(group for group in groups if not group.pilot_authorized)
            now = datetime.now(UTC)
            for group in [chosen, other]:
                db.add(
                    RawMessage(
                        group_id=group.id,
                        telegram_message_id=20 if group == chosen else 21,
                        sender_id=42 if group == chosen else 43,
                        text="private student text",
                        message_date=now,
                        version_date=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
            db.add(
                AnalysisBudgetCall(
                    group_id=chosen.id,
                    attempt_key="real-api-test",
                    kind="text",
                    state="settled",
                    reserved_usd=0.03,
                    charged_usd=0.02,
                    created_at=now,
                    settled_at=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            chosen_id = chosen.id
            await db.commit()
            return chosen_id

    group_id = asyncio.run(seed())
    headers = {"Authorization": "Bearer valid"}
    response = client.get(f"/v1/admin/analytics?days=7&group_id={group_id}", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["summary"]["messages"] == 1
    assert payload["summary"]["chat_authors"] == 1
    assert payload["summary"]["spent_usd"] == 0.02
    assert sum(d["value"] for d in payload["series"]["messages"]) == 1
    assert payload["finance"]["company_balance_usd"] is None
    assert payload["availability"]["app_visits"] == "no_usage_event_collection"
    assert "private student text" not in response.text

    first = client.get(f"/v1/admin/details/messages?group_id={group_id}&limit=1", headers=headers)
    assert first.status_code == 200
    assert len(first.json()["items"]) == 1
    assert not first.json()["has_more"]
    assert "private student text" not in first.text
    costs = client.get(f"/v1/admin/details/expenses?group_id={group_id}", headers=headers)
    assert costs.status_code == 200
    assert costs.json()["items"][0]["fields"]["charged_usd"] == 0.02
    assert client.get(f"/v1/admin/analytics?group_id={uuid4()}", headers=headers).status_code == 404
    assert client.get("/v1/admin/details/does-not-exist", headers=headers).status_code == 404
    assert client.get("/v1/admin/analytics?days=0", headers=headers).status_code == 422
