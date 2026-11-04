"""Approved one-time funding of a single BI 1.2 manual screenshot pilot.

All tests operate on the local test database. No paid HTTP calls.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from test_cabinet_vision_budget_bootstrap import budget, qualify, route
from test_staff_api import db_run

from studgroup.analysis.release_budget import spent
from studgroup.cabinet_vision_budget_topup import RECEIPT, apply
from studgroup.models import AnalysisBudgetCall, Group

pytest_plugins = ["test_schedule_api"]


def topup(client, *, approval=RECEIPT, now=None):
    now = now or datetime.now(UTC)

    async def run(db):
        return await apply(db, approval=approval, now=now)

    return db_run(client, run)


def test_approved_topup_grants_one_extra_dollar_once_without_resetting_ledger(client):
    qualify(client)
    budget(client)
    before = datetime.now(UTC)
    assert topup(client, now=before)["state"] == "configured"
    row = route(client)
    assert row.release_budget_usd == Decimal("1.00")
    assert row.release_budget_receipt == RECEIPT
    assert row.shadow_daily_budget_usd >= Decimal("1.00")
    assert row.release_budget_until == before + timedelta(hours=24)
    assert topup(client, now=before + timedelta(hours=1))["state"] == "already_configured"
    assert route(client).release_budget_until == before + timedelta(hours=24)


def test_topup_counts_prior_paid_attempts_in_total_and_preserves_call_rows(client):
    qualify(client)
    budget(client)

    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        db.add(
            AnalysisBudgetCall(
                group_id=group.id,
                attempt_key="historical",
                kind="cabinet_image",
                state="settled",
                reserved_usd=Decimal(0),
                charged_usd=Decimal("0.02"),
                created_at=datetime.now(UTC),
                delete_at=datetime.now(UTC) + timedelta(days=30),
            )
        )

    db_run(client, seed)
    topup(client)
    assert route(client).release_budget_usd == Decimal("1.02")

    async def verify(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        assert await spent(db, group.id) == Decimal("0.02")
        assert await db.scalar(select(AnalysisBudgetCall.id)) is not None

    db_run(client, verify)


@pytest.mark.parametrize("state", ["reserved", "unknown"])
def test_topup_rejects_unreconciled_paid_attempt(client, state):
    qualify(client)
    budget(client)

    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        db.add(
            AnalysisBudgetCall(
                group_id=group.id,
                attempt_key="uncertain",
                kind="cabinet_image",
                state=state,
                reserved_usd=Decimal("0.01") if state == "reserved" else Decimal(0),
                charged_usd=Decimal(0) if state == "reserved" else Decimal("0.01"),
                created_at=datetime.now(UTC),
                delete_at=datetime.now(UTC) + timedelta(days=30),
            )
        )

    db_run(client, seed)
    with pytest.raises(ValueError, match="unreconciled"):
        topup(client)
    assert route(client).release_budget_usd == Decimal("0.05")


def test_topup_requires_explicit_receipt_and_original_pilot(client):
    qualify(client)
    budget(client)
    with pytest.raises(ValueError, match="approval_mismatch"):
        topup(client, approval="wrong")
    assert route(client).release_budget_usd == Decimal("0.05")
