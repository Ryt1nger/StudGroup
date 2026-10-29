"""Financial gates exercise actual request reservations, not just a configured daily number."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_package_worker import PackageProvider, make_new, seed, step

from studgroup.ai import DeepSeekProvider, ProviderFailure
from studgroup.analysis.release_budget import (
    RequestAccounting,
    configure,
    reported_cost,
    request_ceiling,
    spent,
)
from studgroup.models import AnalysisBudgetCall, Group, GroupAnalysisSettings

pytest_plugins = ["test_schedule_api"]


def payload(*, image=False, text="Read", output=1000):
    content = (
        [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ]
        if image
        else text
    )
    return {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": content}],
        "max_tokens": output,
    }


def setup_budget(client, *, amount=".03", stamp=None):
    make_new(client)
    now = stamp or datetime.now(UTC)

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await configure(
                db,
                group.id,
                amount=amount,
                since=now - timedelta(minutes=5),
                until=now + timedelta(hours=47, minutes=55),
                receipt="a" * 64,
            )
            ident = group.id
            await db.commit()
            return ident

    ident = asyncio.run(run())
    settings = client.app.state.settings
    settings.ai_release_budget_required_groups = [str(ident)]
    return ident, settings, now


def body(prompt=1000, output=1000):
    return {
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": output,
            "prompt_cache_hit_tokens": 0,
        }
    }


def test_funded_text_package_runs_with_legacy_photos_enabled_and_image_feature_off(client):
    from studgroup.main import Settings

    seed(client)
    ident, _, _ = setup_budget(client)
    config = Settings(
        _env_file=None,
        new_engine_enabled=True,
        photo_reading_enabled=True,
        image_analysis_enabled=False,
        ai_release_budget_required_groups=[str(ident)],
    )
    provider = PackageProvider()
    assert step(client, provider, config) == "completed"
    assert len(provider.requests) == 2

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            calls = (await db.scalars(select(AnalysisBudgetCall))).all()
            assert len(calls) == 2 and all(call.state == "settled" for call in calls)
            assert Decimal(0) < await spent(db, ident) < Decimal(".03")

    asyncio.run(inspect())


def test_cancellation_immediately_holds_payment_and_preserves_cancellation(client):
    ident, settings, stamp = setup_budget(client)
    accounting = RequestAccounting(
        client.app.state.engine,
        settings,
        ident,
        kind="cplus",
        attempt_key="cancel",
        clock=lambda: stamp,
    )

    async def run():
        async def interrupted():
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await accounting.execute(payload(), interrupted)
        async with AsyncSession(client.app.state.engine) as db:
            assert (await db.get(GroupAnalysisSettings, ident)).paid_outcome_hold
            call = await db.scalar(select(AnalysisBudgetCall))
            assert call.state == "unknown" and call.reserved_usd == 0
            assert call.charged_usd == request_ceiling(payload())
        with pytest.raises(ProviderFailure, match="release_paid_outcome_held"):
            await accounting.execute(payload(), interrupted)

    asyncio.run(run())


def test_shared_lifetime_allowance_survives_midnight_restart_and_mode_change(client):
    stamp = datetime.now(UTC).replace(hour=23, minute=59, second=0, microsecond=0)
    ident, settings, _ = setup_budget(client, amount=".003", stamp=stamp)
    clock = [stamp]
    invoked = []

    async def run():
        async def paid():
            invoked.append(True)
            return body()

        first = RequestAccounting(
            client.app.state.engine,
            settings,
            ident,
            kind="image",
            attempt_key="first",
            clock=lambda: clock[0],
        )
        await first.execute(payload(), paid)
        clock[0] += timedelta(minutes=2)
        async with AsyncSession(client.app.state.engine) as db:
            route = await db.get(GroupAnalysisSettings, ident)
            route.analysis_mode = "shadow"
            await db.commit()
        restarted = RequestAccounting(
            client.app.state.engine,
            settings,
            ident,
            kind="cplus",
            attempt_key="second",
            clock=lambda: clock[0],
        )
        with pytest.raises(ProviderFailure, match="release_budget_exhausted"):
            await restarted.execute(payload(), paid)
        async with AsyncSession(client.app.state.engine) as db:
            assert await spent(db, ident) == Decimal(".0015")
        assert len(invoked) == 1

    asyncio.run(run())


def test_reservations_are_persisted_before_http_and_settlement_is_idempotent(client):
    ident, settings, stamp = setup_budget(client)
    accounting = RequestAccounting(
        client.app.state.engine,
        settings,
        ident,
        kind="image",
        attempt_key="photo",
        clock=lambda: stamp,
    )

    async def run():
        async def paid():
            async with AsyncSession(client.app.state.engine) as db:
                call = await db.scalar(select(AnalysisBudgetCall))
                assert call.state == "reserved" and call.reserved_usd == request_ceiling(
                    payload(image=True)
                )
            return body(100, 10)

        await accounting.execute(payload(image=True), paid)
        async with AsyncSession(client.app.state.engine) as db:
            call = await db.scalar(select(AnalysisBudgetCall))
            identity = call.id
            assert call.state == "settled" and call.reserved_usd == 0
            assert call.charged_usd == reported_cost(body(100, 10)["usage"])
        await accounting._finish(identity, usage=body(100, 10)["usage"])
        async with AsyncSession(client.app.state.engine) as db:
            assert await spent(db, ident) == reported_cost(body(100, 10)["usage"])

    asyncio.run(run())


def test_unknown_outcome_holds_text_vision_and_a_new_process(client):
    ident, settings, stamp = setup_budget(client)
    accounting = RequestAccounting(
        client.app.state.engine,
        settings,
        ident,
        kind="image",
        attempt_key="uncertain",
        clock=lambda: stamp,
    )
    invoked = []

    async def run():
        async def unknown():
            invoked.append(True)
            raise ProviderFailure("provider_unreachable", True)

        with pytest.raises(ProviderFailure, match="ambiguous_paid_attempt"):
            await accounting.execute(payload(image=True), unknown)
        for kind in ["image", "cplus", "legacy"]:
            other = RequestAccounting(
                client.app.state.engine,
                settings,
                ident,
                kind=kind,
                attempt_key="later",
                clock=lambda: stamp,
            )
            with pytest.raises(ProviderFailure, match="release_paid_outcome_held"):
                await other.execute(payload(), unknown)
        async with AsyncSession(client.app.state.engine) as db:
            call = await db.scalar(select(AnalysisBudgetCall))
            assert call.state == "unknown" and call.charged_usd == request_ceiling(
                payload(image=True)
            )
            assert call.reserved_usd == 0
        assert len(invoked) == 1

    asyncio.run(run())


def test_proven_preconnection_failure_releases_the_money_and_allows_a_bounded_retry(client):
    ident, settings, stamp = setup_budget(client)
    accounting = RequestAccounting(
        client.app.state.engine,
        settings,
        ident,
        kind="cplus",
        attempt_key="connect",
        clock=lambda: stamp,
    )

    async def run():
        async def offline():
            raise ProviderFailure("provider_unreachable", True, reservation_releasable=True)

        with pytest.raises(ProviderFailure):
            await accounting.execute(payload(), offline)
        async with AsyncSession(client.app.state.engine) as db:
            assert await spent(db, ident) == 0
            assert (await db.get(GroupAnalysisSettings, ident)).paid_outcome_hold is None

        async def success():
            return body(100, 10)

        await accounting.execute(payload(), success)
        async with AsyncSession(client.app.state.engine) as db:
            assert len((await db.scalars(select(AnalysisBudgetCall))).all()) == 2

    asyncio.run(run())


def test_a_lost_request_remains_charged_and_never_auto_retries(client):
    ident, settings, stamp = setup_budget(client)
    clock = [stamp]
    accounting = RequestAccounting(
        client.app.state.engine,
        settings,
        ident,
        kind="cplus",
        attempt_key="lost",
        clock=lambda: clock[0],
    )

    async def run():
        await accounting._begin(payload())  # committed, then the process died
        clock[0] += timedelta(minutes=4)

        async def never():
            pytest.fail("A lost financial reservation must block every paid request")

        with pytest.raises(ProviderFailure, match="release_paid_outcome_held"):
            await accounting.execute(payload(), never)
        async with AsyncSession(client.app.state.engine) as db:
            call = await db.scalar(select(AnalysisBudgetCall))
            assert call.state == "unknown" and call.charged_usd > 0 and call.reserved_usd == 0

    asyncio.run(run())


def test_window_expiry_and_required_missing_budget_fail_before_provider(client):
    ident, settings, stamp = setup_budget(client)

    async def run():
        async def never():
            pytest.fail("Missing/expired release funding must not reach a provider")

        expired = RequestAccounting(
            client.app.state.engine,
            settings,
            ident,
            kind="cplus",
            attempt_key="expired",
            clock=lambda: stamp + timedelta(days=2),
        )
        with pytest.raises(ProviderFailure, match="release_budget_window_closed"):
            await expired.execute(payload(), never)
        async with AsyncSession(client.app.state.engine) as db:
            route = await db.get(GroupAnalysisSettings, ident)
            route.release_budget_usd = None
            await db.commit()
        missing = RequestAccounting(
            client.app.state.engine,
            settings,
            ident,
            kind="image",
            attempt_key="missing",
            clock=lambda: stamp,
        )
        with pytest.raises(ProviderFailure, match="release_budget_not_configured"):
            await missing.execute(payload(image=True), never)

    asyncio.run(run())


def test_funding_cannot_be_reset_or_increased_by_reconfiguration(client):
    ident, _settings, stamp = setup_budget(client)

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            await configure(
                db,
                ident,
                amount=".03",
                since=stamp - timedelta(minutes=5),
                until=stamp + timedelta(hours=47, minutes=55),
                receipt="a" * 64,
            )
            with pytest.raises(ValueError, match="release_budget_cannot_reset"):
                await configure(
                    db,
                    ident,
                    amount=".04",
                    since=stamp,
                    until=stamp + timedelta(hours=48),
                    receipt="b" * 64,
                )

    asyncio.run(run())


def test_production_transport_hook_accounts_each_physical_request(client):
    import httpx

    ident, settings, stamp = setup_budget(client)
    accounting = RequestAccounting(
        client.app.state.engine,
        settings,
        ident,
        kind="legacy",
        attempt_key="transport",
        clock=lambda: stamp,
    )

    def response(_request):
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                **body(100, 10),
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            },
        )

    async def run():
        provider = DeepSeekProvider(
            "offline-test", transport=httpx.MockTransport(response), request_accounting=accounting
        )
        await provider._complete(payload())
        async with AsyncSession(client.app.state.engine) as db:
            assert len((await db.scalars(select(AnalysisBudgetCall))).all()) == 1
            assert await spent(db, ident) == reported_cost(body(100, 10)["usage"])

    asyncio.run(run())
