"""Every authorized group's paused backlog remains untouched and visible."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings
from studgroup.models import Group, GroupAnalysisSettings, MessageAttachment, RawMessage
from studgroup.processing_health import inspect

pytest_plugins = ["test_schedule_api"]


def test_paused_backlog_detected_without_changing_sources_or_spending(client):
    async def run():
        now = datetime.now(UTC)
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.pilot_authorized = True
            group.status = "active"
            group.bot_added_at = now - timedelta(days=1)
            route = GroupAnalysisSettings(
                group_id=group.id,
                analysis_mode="new",
                engine_version="cplus-shadow-1",
                shadow_daily_budget_usd=Decimal("0.10"),
                shadow_sample_percent=100,
                release_budget_usd=Decimal("0.10"),
                release_budget_since=now - timedelta(days=2),
                release_budget_until=now - timedelta(minutes=1),
                updated_at=now,
            )
            db.add(route)
            raw = RawMessage(
                id=uuid.uuid4(),
                group_id=group.id,
                telegram_message_id=807,
                text="ДЗ на завтра",
                message_date=now - timedelta(minutes=5),
                version_date=now - timedelta(minutes=5),
                live_received_at=now - timedelta(minutes=5),
                imported=False,
                processing_state="pending",
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.commit()
            config = Settings(
                _env_file=None,
                new_engine_enabled=True,
                ai_release_budget_required_groups=[str(group.id)],
            )
            paused = await inspect(client.app.state.engine, config, now)
            assert paused.paused is True
            assert paused.paused_groups == 1
            assert paused.pending_messages == 1
            assert paused.reasons == ("release_budget_window_closed",)
            await db.refresh(raw)
            assert raw.processing_state == "pending"

            # When an owner-authorized window is active, the same pending source
            # becomes eligible for the existing durable worker without re-import.
            route.release_budget_until = now + timedelta(hours=1)
            await db.commit()
            resumed = await inspect(client.app.state.engine, config, now)
            assert resumed.paused is False
            await db.refresh(raw)
            assert raw.processing_state == "pending"

            off = await inspect(
                client.app.state.engine,
                Settings(_env_file=None, new_engine_enabled=False),
                now,
            )
            assert off.reasons == ("engine_disabled",)
            cutoff = await inspect(
                client.app.state.engine,
                Settings(
                    _env_file=None,
                    new_engine_enabled=True,
                    new_engine_enabled_until=now - timedelta(seconds=1),
                ),
                now,
            )
            assert cutoff.reasons == ("engine_window_closed",)

            # Imported history and expired source rows must not count as recoverable.
            raw.imported = True
            await db.commit()
            imported = await inspect(client.app.state.engine, config, now)
            assert imported.paused is False
            raw.imported = False
            raw.delete_at = now - timedelta(seconds=1)
            await db.commit()
            expired = await inspect(client.app.state.engine, config, now)
            assert expired.paused is False

            raw.delete_at = now + timedelta(days=30)
            attachment = MessageAttachment(
                raw_message_id=raw.id,
                group_id=group.id,
                source_revision=1,
                kind="photo",
                stable_file_id="unread-807",
                telegram_file_id="telegram-807",
                mime_type="image/jpeg",
                processing_state="metadata_only",
                active=True,
                created_at=now,
                delete_at=now + timedelta(days=30),
            )
            db.add(attachment)
            await db.commit()
            paused_photo = await inspect(
                client.app.state.engine,
                Settings(
                    _env_file=None,
                    new_engine_enabled=True,
                    photo_reading_enabled=True,
                    ai_release_budget_required_groups=[str(group.id)],
                ),
                now,
            )
            assert paused_photo.reasons == ("photo_reader_unavailable",)
            assert paused_photo.pending_messages == 1
            await db.refresh(raw)
            assert raw.processing_state == "pending"
            attachment.processing_state = "ready"
            await db.commit()
            ready_photo = await inspect(
                client.app.state.engine,
                Settings(
                    _env_file=None,
                    new_engine_enabled=True,
                    photo_reading_enabled=True,
                    ai_release_budget_required_groups=[str(group.id)],
                ),
                now,
            )
            assert ready_photo.paused is False

    asyncio.run(run())
