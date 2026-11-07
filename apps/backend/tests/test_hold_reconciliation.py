"""Only terminal, precisely matched paid attempts may release old group-wide holds."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.hold_reconciliation import reconcile_terminal_holds
from studgroup.main import Settings
from studgroup.models import (
    AnalysisBudgetCall,
    Group,
    GroupAnalysisSettings,
    ImageAnalysisCall,
    ImageAnalysisJob,
    MessageAttachment,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]


def test_terminal_vision_hold_recovers_without_erasing_or_replaying_payment(client):
    async def prepare():
        now = datetime.now(UTC)
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.status, group.pilot_authorized = "active", True
            db.add(
                GroupAnalysisSettings(
                    group_id=group.id,
                    analysis_mode="new",
                    engine_version="cplus-shadow-1",
                    shadow_daily_budget_usd=Decimal("0.10"),
                    shadow_sample_percent=100,
                    paid_outcome_hold="ambiguous_paid_attempt",
                    updated_at=now,
                )
            )
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=600,
                text="Смотрите фотографию с заданием",
                message_date=now,
                version_date=now,
                revision=1,
                imported=False,
                processing_state="pending",
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.flush()
            attachment = MessageAttachment(
                raw_message_id=raw.id,
                group_id=group.id,
                source_revision=1,
                kind="photo",
                stable_file_id="terminal-image-600",
                telegram_file_id="f600",
                mime_type="image/jpeg",
                processing_state="metadata_only",
                active=True,
                created_at=now,
                delete_at=now + timedelta(days=30),
            )
            db.add(attachment)
            await db.flush()
            token = uuid.uuid4()
            job = ImageAnalysisJob(
                group_id=group.id,
                attachment_id=attachment.id,
                raw_message_id=raw.id,
                source_revision=1,
                context_sha256="a" * 64,
                context_json="{}",
                version="test-pass-b",
                model_key="test",
                pass_number=2,
                state="running",
                attempts=1,
                available_at=now - timedelta(minutes=5),
                lease_until=now + timedelta(minutes=5),
                claim_token=token,
                created_at=now,
                delete_at=now + timedelta(days=30),
            )
            db.add(job)
            await db.flush()
            db.add_all(
                [
                    ImageAnalysisCall(
                        job_id=job.id,
                        group_id=group.id,
                        claim_token=token,
                        state="unknown",
                        reserved_usd=Decimal(0),
                        charged_usd=Decimal("0.02"),
                        created_at=now,
                    ),
                    AnalysisBudgetCall(
                        group_id=group.id,
                        attempt_key=f"image:{token}",
                        kind="image",
                        state="unknown",
                        reserved_usd=Decimal(0),
                        charged_usd=Decimal("0.02357130"),
                        created_at=now,
                        settled_at=now,
                        delete_at=now + timedelta(days=365),
                    ),
                ]
            )
            await db.commit()
            return group.id, job.id

    ident, job_id = asyncio.run(prepare())
    settings = Settings(_env_file=None, ai_production_groups=[str(ident)])

    async def run():
        engine = client.app.state.engine
        # The original attempt is still running: never clear the hold.
        assert await reconcile_terminal_holds(engine, settings) == 0
        async with AsyncSession(engine) as db:
            job = await db.get(ImageAnalysisJob, job_id)
            job.state, job.error, job.lease_until = (
                "stale",
                "context_or_source_revision_changed",
                None,
            )
            await db.commit()
        assert await reconcile_terminal_holds(engine, Settings(_env_file=None)) == 0
        assert await reconcile_terminal_holds(engine, settings) == 1
        assert await reconcile_terminal_holds(engine, settings) == 0
        async with AsyncSession(engine) as db:
            route = await db.get(GroupAnalysisSettings, ident)
            assert route.paid_outcome_hold is None
            ledger = (await db.scalars(select(AnalysisBudgetCall))).all()
            assert len(ledger) == 1
            assert ledger[0].state == "unknown"
            assert ledger[0].charged_usd == Decimal("0.02357130")
            assert ledger[0].reserved_usd == 0
            source = await db.scalar(
                select(RawMessage).where(RawMessage.telegram_message_id == 600)
            )
            assert source.processing_state == "pending"

    asyncio.run(run())
