"""Operator bot monitoring uses real database signals and no private message content."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import BotOutbox, BotPanel, Group, RawMessage, ReceivedUpdate

pytest_plugins = ["test_schedule_api"]


def test_bot_monitor_requires_owner(client):
    assert client.get("/v1/admin/bot-monitor").status_code == 401
    assert (
        client.get("/v1/admin/bot-monitor", headers={"Authorization": "Bearer valid"}).status_code
        == 403
    )


def test_bot_monitor_splits_global_and_group_counts(client):
    client.app.state.settings.bot_admin_user_id = 1

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            groups = (await db.scalars(select(Group))).all()
            selected = next(x for x in groups if x.pilot_authorized)
            other = next(x for x in groups if not x.pilot_authorized)
            now = datetime.now(UTC)
            db.add(ReceivedUpdate(update_id=997811, received_at=now))
            for g, mid in [(selected, 9911), (other, 9912)]:
                db.add(
                    RawMessage(
                        group_id=g.id,
                        telegram_message_id=mid,
                        sender_id=42,
                        text="secret student message",
                        message_date=now,
                        version_date=now,
                        delete_at=now + timedelta(days=30),
                        imported=False,
                    )
                )
            db.add(
                BotOutbox(
                    id=uuid4(),
                    method="sendMessage",
                    payload="secret outgoing message",
                    state="failed",
                    attempts=2,
                    available_at=now,
                    dedup_key="admin-bot-monitor-test",
                )
            )
            db.add(
                BotPanel(
                    id=uuid4(), chat_id=99784444, mode="student",
                    generation=1, revision=0,
                )
            )
            gid = selected.id
            await db.commit()
            return gid

    gid = asyncio.run(seed())
    headers = {"Authorization": "Bearer valid"}
    response = client.get(f"/v1/admin/bot-monitor?days=7&group_id={gid}", headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["messages"]["period_count"] == 1
    assert sum(row["messages"] for row in result["daily"]) == 1
    assert result["bot"]["webhook_updates"] >= 1
    assert sum(row["updates"] for row in result["daily"]) >= 1
    assert result["bot"]["failed"] >= 1
    assert result["bot"]["panel_total"] >= 1
    assert result["people"]["active_memberships"] >= 0
    assert "secret student message" not in response.text
    assert "secret outgoing message" not in response.text
    assert "99784444" not in response.text
    assert result["source_notes"]["outbox"]

    for kind in ("bot_updates", "bot_panels", "bot_sessions"):
        details = client.get(f"/v1/admin/details/{kind}?days=7", headers=headers)
        assert details.status_code == 200, details.text
        assert "secret outgoing message" not in details.text
        assert "99784444" not in details.text

    assert client.get("/v1/admin/bot-monitor?days=0", headers=headers).status_code == 422


def test_operator_console_contains_seven_separate_pages(client):
    html = client.get("/admin/")
    script = client.get("/admin/app.js")
    css = client.get("/admin/style.css")
    assert html.status_code == script.status_code == css.status_code == 200
    assert html.text.count('data-screen="') == 7
    assert html.text.count('data-nav="') == 7
    assert 'id="chart-bot-updates"' in html.text
    assert 'id="chart-bot-delivery"' in html.text
    assert "bot-monitor?" in script.text
    assert "selectPage(" in script.text
    assert "backdrop-filter:blur" in css.text
    assert "--bg:#060709" in css.text
