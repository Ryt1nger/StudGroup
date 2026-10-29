"""Source-independent release funding. Every physical request reserves before HTTP and settles once.

Peak byte/token bounds: https://api-docs.deepseek.com/guides/vision/ (1024/image) and pricing.
No prompts, images, secrets or user text are persisted here. Group-route locks serialize text and
vision reservations; midnight, retries, source purge and mode changes never reset the allowance.
"""

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import ProviderFailure
from studgroup.models import AnalysisBudgetCall, Group, GroupAnalysisSettings

QUANTUM = Decimal(".00000001")
PEAK_INPUT = Decimal(".30") / 1000000
PEAK_HIT = Decimal(".006") / 1000000
PEAK_OUTPUT = Decimal("1.20") / 1000000
MAX_WINDOW = timedelta(hours=48)
UNRESOLVED_AFTER = timedelta(minutes=3)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def ceil_usd(value):
    return Decimal(value).quantize(QUANTUM, rounding=ROUND_CEILING)


def request_ceiling(payload):
    if payload.get("model") != "deepseek-flash":
        raise ValueError("release_budget_model_unverified")
    text_bytes, images = 4096, 0
    for message in payload["messages"]:
        content = message["content"]
        if isinstance(content, str):
            text_bytes += len(content.encode())
        else:
            for block in content:
                if block["type"] == "image_url":
                    images += 1
                elif block["type"] == "text":
                    text_bytes += len(block["text"].encode())
                else:
                    raise ValueError("release_budget_content_unpriced")
    maximum = payload["max_tokens"]
    if not isinstance(maximum, int) or not 0 < maximum <= 8000:
        raise ValueError("release_budget_output_unbounded")
    return ceil_usd((text_bytes + images * 1024) * PEAK_INPUT + maximum * PEAK_OUTPUT)


def reported_cost(usage):
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
    hit = usage.get("prompt_cache_hit_tokens", 0)
    if (
        any(not isinstance(value, int) or value < 0 for value in (prompt, completion, hit))
        or hit > prompt
    ):
        raise ValueError("invalid_usage")
    return ceil_usd((prompt - hit) * PEAK_INPUT + hit * PEAK_HIT + completion * PEAK_OUTPUT)


async def configure(db, group_id, *, amount, since, until, receipt):
    """Trusted operator only, after funding is reserved in the same local USD-1 journal.

    Idempotent exact retries are allowed. There is intentionally no reset/increase/unhold API.
    """
    amount, since, until = Decimal(amount), utc(since), utc(until)
    if amount != ceil_usd(amount) or not 0 <= amount <= 1:
        raise ValueError("invalid_release_funding")
    if not timedelta(0) < until - since <= MAX_WINDOW or not re.fullmatch(r"[0-9a-f]{64}", receipt):
        raise ValueError("invalid_release_scope")
    route = await db.scalar(
        select(GroupAnalysisSettings)
        .where(GroupAnalysisSettings.group_id == group_id)
        .with_for_update()
    )
    group = await db.get(Group, group_id)
    if route is None or group is None or group.status != "active" or not group.pilot_authorized:
        raise ValueError("release_group_not_authorized")
    if route.paid_outcome_hold:
        raise ValueError("release_paid_outcome_requires_review")
    if route.release_budget_usd is not None:
        if (
            route.release_budget_usd == amount
            and utc(route.release_budget_since) == since
            and utc(route.release_budget_until) == until
            and route.release_budget_receipt == receipt
        ):
            return route
        raise ValueError("release_budget_cannot_reset")
    if await db.scalar(
        select(AnalysisBudgetCall.id).where(AnalysisBudgetCall.group_id == group_id).limit(1)
    ):
        raise ValueError("release_ledger_requires_review")
    route.release_budget_usd, route.release_budget_since, route.release_budget_until = (
        amount,
        since,
        until,
    )
    route.release_budget_receipt = receipt
    return route


async def spent(db, group_id, *, since=None):
    query = select(
        func.coalesce(func.sum(AnalysisBudgetCall.charged_usd + AnalysisBudgetCall.reserved_usd), 0)
    ).where(AnalysisBudgetCall.group_id == group_id)
    if since is not None:
        query = query.where(AnalysisBudgetCall.created_at >= since)
    return Decimal(await db.scalar(query) or 0)


class RequestAccounting:
    def __init__(self, engine, settings, group_id, *, kind, attempt_key, mode=None, clock=None):
        self.engine, self.settings, self.group_id = engine, settings, group_id
        self.kind, self.attempt_key, self.mode = kind, str(attempt_key), mode
        self.clock = clock or (lambda: datetime.now(UTC))

    async def _begin(self, payload):
        now = utc(self.clock())
        reason, ident = None, None
        async with AsyncSession(self.engine) as db:
            route = await db.scalar(
                select(GroupAnalysisSettings)
                .where(GroupAnalysisSettings.group_id == self.group_id)
                .with_for_update()
            )
            required = str(self.group_id) in self.settings.ai_release_budget_required_groups
            if route is None:
                if required:
                    reason = "release_budget_not_configured"
            elif route.paid_outcome_hold:
                reason = "release_paid_outcome_held"
            elif required and route.release_budget_usd is None:
                reason = "release_budget_not_configured"
            elif (self.mode and route.analysis_mode != self.mode) or (
                self.kind == "image" and route.analysis_mode not in {"new", "shadow"}
            ):
                reason = "release_mode_changed"
            elif route.release_budget_usd is not None:
                group = await db.get(Group, self.group_id)
                if group is None or not group.pilot_authorized or group.status != "active":
                    reason = "release_group_not_authorized"
                elif (
                    route.release_budget_since is None
                    or route.release_budget_until is None
                    or not route.release_budget_receipt
                    or not utc(route.release_budget_since) <= now < utc(route.release_budget_until)
                    or utc(route.release_budget_until) - utc(route.release_budget_since)
                    > MAX_WINDOW
                ):
                    reason = "release_budget_window_closed"
                else:
                    lost = (
                        await db.scalars(
                            select(AnalysisBudgetCall)
                            .where(
                                AnalysisBudgetCall.group_id == self.group_id,
                                AnalysisBudgetCall.state == "reserved",
                                AnalysisBudgetCall.created_at <= now - UNRESOLVED_AFTER,
                            )
                            .with_for_update()
                        )
                    ).all()
                    for call in lost:
                        call.charged_usd, call.reserved_usd = call.reserved_usd, Decimal(0)
                        call.state, call.settled_at = "unknown", now
                    if lost:
                        route.paid_outcome_hold = "ambiguous_paid_attempt"
                        reason = "release_paid_outcome_held"
                    else:
                        try:
                            ceiling = request_ceiling(payload)
                        except (ValueError, KeyError, TypeError):
                            reason = "release_request_unpriced"
                        if reason is None:
                            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
                            hourly_cap = min(
                                Decimal(str(self.settings.image_hourly_budget_usd)),
                                Decimal(str(self.settings.new_engine_hourly_budget_usd)),
                            )
                            if await spent(db, self.group_id) + ceiling > route.release_budget_usd:
                                reason = "release_budget_exhausted"
                            elif (
                                await spent(db, self.group_id, since=day) + ceiling
                                > route.shadow_daily_budget_usd
                            ):
                                reason = "release_daily_budget_exhausted"
                            elif (
                                await spent(db, self.group_id, since=now - timedelta(hours=1))
                                + ceiling
                                > hourly_cap
                            ):
                                reason = "release_hourly_budget_exhausted"
                            else:
                                ident = uuid.uuid4()
                                db.add(
                                    AnalysisBudgetCall(
                                        id=ident,
                                        group_id=self.group_id,
                                        kind=self.kind,
                                        attempt_key=self.attempt_key,
                                        state="reserved",
                                        reserved_usd=ceiling,
                                        charged_usd=Decimal(0),
                                        created_at=now,
                                        delete_at=utc(route.release_budget_until)
                                        + timedelta(days=30),
                                    )
                                )
            await db.commit()  # unknown holds must survive the following exception
        if reason:
            raise ProviderFailure(reason, False, reservation_releasable=True)
        return ident

    async def _finish(self, ident, *, usage=None, releasable=False):
        now = utc(self.clock())
        unknown = usage is None and not releasable
        try:
            actual = reported_cost(usage) if usage is not None else Decimal(0)
        except (ValueError, KeyError, TypeError):
            actual, unknown = Decimal(0), True
        async with AsyncSession(self.engine) as db:
            route = await db.scalar(
                select(GroupAnalysisSettings)
                .where(GroupAnalysisSettings.group_id == self.group_id)
                .with_for_update()
            )
            if route is None:
                return
            if unknown:
                route.paid_outcome_hold = "ambiguous_paid_attempt"
            if ident is not None:
                call = await db.get(AnalysisBudgetCall, ident, with_for_update=True)
                if call is None:
                    route.paid_outcome_hold = "release_financial_record_missing"
                elif call.state != "settled":
                    ceiling = call.reserved_usd if call.state == "reserved" else call.charged_usd
                    call.charged_usd = max(actual, ceiling) if unknown else actual
                    call.reserved_usd, call.settled_at = Decimal(0), now
                    call.state = "unknown" if unknown else "settled"
                    if actual > ceiling:
                        route.paid_outcome_hold = "provider_charge_over_reservation"
            await db.commit()
        if unknown:
            raise ProviderFailure("ambiguous_paid_attempt", False)

    async def execute(self, payload, invoke):
        ident = await self._begin(payload)
        try:
            response = await invoke()
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self._finish(ident))
            except ProviderFailure:
                pass
            raise
        except ProviderFailure as error:
            await self._finish(ident, usage=error.usage, releasable=error.reservation_releasable)
            raise
        except Exception:
            await self._finish(ident)
            raise
        usage = response.get("usage") if isinstance(response, dict) else response.usage
        await self._finish(ident, usage=usage)
        return response


class AccountedPort:
    """Offline/custom provider adapter; the production provider accounts every raw HTTP attempt."""

    def __init__(self, provider, accounting, model="deepseek-flash"):
        self.provider, self.accounting, self.model = provider, accounting, model

    async def complete(self, request):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": json.dumps(request.user_payload, ensure_ascii=False)},
            ],
            "max_tokens": request.max_output_tokens,
        }
        return await self.accounting.execute(payload, lambda: self.provider.complete(request))

    async def complete_vision(self, **request):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request["system_prompt"]},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": request["user_text"]},
                        {"type": "image_url", "image_url": {"url": request["image_data_url"]}},
                    ],
                },
            ],
            "max_tokens": request["max_output_tokens"],
        }
        return await self.accounting.execute(
            payload, lambda: self.provider.complete_vision(**request)
        )


def provider_for(engine, settings, group_id, provider=None, *, kind, attempt_key, mode=None):
    accounting = RequestAccounting(
        engine, settings, group_id, kind=kind, attempt_key=attempt_key, mode=mode
    )
    if provider is not None:
        return AccountedPort(provider, accounting, settings.deepseek_model)
    from studgroup.ai import DeepSeekProvider

    return DeepSeekProvider(
        settings.deepseek_api_key.get_secret_value(),
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
        allow_technical_repair=settings.ai_technical_repair_enabled,
        request_accounting=accounting,
    )


async def hold_lost_attempt(db, route, attempt_key, now):
    """Caller owns the route lock. A lost lease is not permission to purchase another attempt."""
    route.paid_outcome_hold = "ambiguous_paid_attempt"
    calls = (
        await db.scalars(
            select(AnalysisBudgetCall)
            .where(
                AnalysisBudgetCall.group_id == route.group_id,
                AnalysisBudgetCall.attempt_key == attempt_key,
                AnalysisBudgetCall.state == "reserved",
            )
            .with_for_update()
        )
    ).all()
    for call in calls:
        call.charged_usd, call.reserved_usd = call.reserved_usd, Decimal(0)
        call.state, call.settled_at = "unknown", now
