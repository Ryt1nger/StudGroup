"""Durable image worker. No publication imports; all paid calls follow committed reservations."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import PurePosixPath
from time import monotonic

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import ProviderFailure
from studgroup.analysis.image_contracts import IMAGE_VERSION, VisualReading
from studgroup.analysis.photo_reading import MAX_IMAGE_BYTES, TELEGRAM_API, read_photo, sniff_mime
from studgroup.analysis.shadow import usage_cost_usd
from studgroup.models import (
    Group,
    GroupAnalysisSettings,
    ImageAnalysisCall,
    ImageAnalysisJob,
    MessageAttachment,
    RawMessage,
    ShadowEvaluationRun,
)

TERMINAL = {"ready", "failed", "stale"}
MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=3)
SUPPORTED = {"image/jpeg", "image/png", "image/webp"}


def utc(stamp):
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def enabled(settings, group_id):
    return settings.image_analysis_enabled and str(group_id) in settings.image_analysis_groups


def supported(attachment):
    return attachment.kind == "photo" or (
        attachment.kind == "document" and (
            (attachment.mime_type or "").lower().startswith("image/")
            or (attachment.file_name or "").lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        )
    )


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def context_snapshot(db, raw):
    before = (await db.scalars(select(RawMessage).where(
        RawMessage.group_id == raw.group_id, RawMessage.message_date <= raw.message_date,
        RawMessage.delete_at > datetime.now(UTC),
    ).order_by(RawMessage.message_date.desc(), RawMessage.telegram_message_id.desc()).limit(11))).all()
    after = (await db.scalars(select(RawMessage).where(
        RawMessage.group_id == raw.group_id, RawMessage.message_date > raw.message_date,
        RawMessage.delete_at > datetime.now(UTC),
    ).order_by(RawMessage.message_date, RawMessage.telegram_message_id).limit(8))).all()
    related = (await db.scalars(select(RawMessage).where(
        RawMessage.group_id == raw.group_id, RawMessage.delete_at > datetime.now(UTC),
        or_(RawMessage.telegram_message_id == raw.reply_to_message_id,
            RawMessage.reply_to_message_id == raw.telegram_message_id),
    ).order_by(RawMessage.message_date).limit(40))).all()
    rows = {row.id: row for row in [*before, raw, *after, *related]}
    return {"messages": [
        {"id": row.telegram_message_id, "revision": row.revision, "text": row.text,
         "reply_to": row.reply_to_message_id, "topic": row.message_thread_id,
         "sent_at": utc(row.message_date).isoformat()}
        for row in sorted(rows.values(), key=lambda row: (row.message_date, row.telegram_message_id))
    ]}


async def schedule_group(db, group, now):
    """Caller holds group settings lock. Enumerate every retained image, not just six."""
    attachments = (await db.scalars(select(MessageAttachment).join(
        RawMessage, RawMessage.id == MessageAttachment.raw_message_id
    ).where(
        MessageAttachment.group_id == group.id, MessageAttachment.active.is_(True),
        MessageAttachment.delete_at > now, RawMessage.group_id == group.id,
        RawMessage.delete_at > now,
        RawMessage.message_date >= (group.bot_added_at or now - timedelta(days=30)),
    ).order_by(MessageAttachment.created_at, MessageAttachment.id))).all()
    created = 0
    for attachment in attachments:
        if not supported(attachment):
            continue
        raw = await db.get(RawMessage, attachment.raw_message_id)
        key = digest({"file": attachment.stable_file_id, "revision": raw.revision, "version": IMAGE_VERSION})
        jobs = (await db.scalars(select(ImageAnalysisJob).where(
            ImageAnalysisJob.attachment_id == attachment.id,
        ))).all()
        for job in jobs:
            if job.source_revision != raw.revision and job.state != "stale":
                job.state, job.error = "stale", "source_revision_changed"
        if any(job.context_sha256 == key and job.pass_number == 1 for job in jobs):
            continue
        db.add(ImageAnalysisJob(
            group_id=group.id, attachment_id=attachment.id, raw_message_id=raw.id,
            source_revision=raw.revision, context_sha256=key, context_json="{}",
            version=IMAGE_VERSION, pass_number=1, state="pending", attempts=0,
            available_at=now, created_at=now, delete_at=min(raw.delete_at, attachment.delete_at),
        ))
        created += 1
    return created


class DownloadError(Exception):
    def __init__(self, code, retryable):
        self.code, self.retryable = code, retryable


async def download(token, file_id, *, transport=None):
    """Bound streamed bytes, validate Telegram file path, retain retryable/terminal reasons."""
    if not token:
        raise DownloadError("telegram_token_missing", False)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, transport=transport) as client:
            info = await client.post(f"{TELEGRAM_API}/bot{token}/getFile", json={"file_id": file_id})
            if info.status_code != 200:
                raise DownloadError("telegram_get_file", info.status_code == 429 or info.status_code >= 500)
            result = info.json().get("result") or {}
            path = result.get("file_path")
            if (not isinstance(path, str) or not path or ".." in PurePosixPath(path).parts
                    or path.startswith("/") or ":" in path or "?" in path or "#" in path):
                raise DownloadError("telegram_invalid_path", False)
            if int(result.get("file_size") or 0) > MAX_IMAGE_BYTES:
                raise DownloadError("image_too_large", False)
            content = bytearray()
            async with client.stream("GET", f"{TELEGRAM_API}/file/bot{token}/{path}") as response:
                if response.status_code != 200:
                    raise DownloadError("telegram_download", response.status_code == 429 or response.status_code >= 500)
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_IMAGE_BYTES:
                        raise DownloadError("image_too_large", False)
    except (httpx.HTTPError, ValueError, TypeError):
        raise DownloadError("telegram_unavailable", True) from None
    image = bytes(content)
    mime = sniff_mime(image)
    if mime not in SUPPORTED:
        raise DownloadError("unsupported_image_format", False)
    return image, mime


async def total(db, model, column, *filters):
    return Decimal(await db.scalar(select(func.coalesce(func.sum(column), 0)).select_from(model).where(*filters)) or 0)


async def claim(db, group, route, settings, now):
    job = await db.scalar(select(ImageAnalysisJob).where(
        ImageAnalysisJob.group_id == group.id, ImageAnalysisJob.delete_at > now,
        or_(
            ImageAnalysisJob.state.in_(["pending", "retry", "budget_deferred"]) & (ImageAnalysisJob.available_at <= now),
            (ImageAnalysisJob.state == "running") & (ImageAnalysisJob.lease_until <= now),
        ),
    ).order_by(ImageAnalysisJob.created_at, ImageAnalysisJob.pass_number, ImageAnalysisJob.id)
        .with_for_update(skip_locked=True).limit(1))
    if job is None:
        return None
    if job.state == "running":
        # Unknown external outcome: do not silently purchase it again.
        call = await db.scalar(select(ImageAnalysisCall).where(ImageAnalysisCall.claim_token == job.claim_token))
        if call is not None and call.state == "reserved":
            call.state, call.charged_usd = "unknown", call.reserved_usd
            call.reserved_usd = Decimal(0)
        job.state, job.error, job.lease_until = "failed", "ambiguous_paid_attempt", None
        return None
    raw = await db.get(RawMessage, job.raw_message_id)
    attachment = await db.get(MessageAttachment, job.attachment_id)
    if (raw is None or attachment is None or not attachment.active or raw.revision != job.source_revision
            or utc(raw.delete_at) <= now or utc(attachment.delete_at) <= now):
        job.state, job.error = "stale", "source_revision_changed"
        return None
    reserve = Decimal(str(settings.image_pass_reservation_usd))
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    group_filter = ImageAnalysisCall.group_id == group.id
    committed = await total(db, ImageAnalysisCall,
        ImageAnalysisCall.charged_usd + ImageAnalysisCall.reserved_usd,
        group_filter, ImageAnalysisCall.created_at >= day_start)
    text_spent = await total(db, ShadowEvaluationRun,
        ShadowEvaluationRun.charged_usd + ShadowEvaluationRun.reserved_usd,
        ShadowEvaluationRun.group_id == group.id, ShadowEvaluationRun.created_at >= day_start)
    image_jobs = select(ImageAnalysisJob.id).where(ImageAnalysisJob.attachment_id == attachment.id)
    image_spent = await total(db, ImageAnalysisCall,
        ImageAnalysisCall.charged_usd + ImageAnalysisCall.reserved_usd,
        ImageAnalysisCall.job_id.in_(image_jobs))
    if reserve <= 0 or committed + text_spent + reserve > Decimal(route.shadow_daily_budget_usd):
        job.state, job.error = "budget_deferred", "image_group_budget"
        job.available_at = day_start + timedelta(days=1)
        return None
    if image_spent + reserve > Decimal(str(settings.image_total_budget_usd)):
        job.state, job.error = "failed", "image_budget_exhausted"
        return None
    token = uuid.uuid4()
    job.state, job.claim_token = "running", token
    job.attempts += 1
    job.lease_until = now + LEASE
    job.error = None
    db.add(ImageAnalysisCall(job_id=job.id, group_id=group.id, claim_token=token,
        state="reserved", reserved_usd=reserve, charged_usd=0, created_at=now))
    return job.id, token


async def perform(provider, image, mime, job, raw, first=None):
    # Stage 1 compatibility reader. Replaced by structured two-pass reader in stage 2a.
    result = await read_photo(provider, image, mime, raw.text)
    return VisualReading(summary=result.summary, extracted_text=result.text, complete=True), result.usage


async def process_images_next(engine, settings, provider=None, now=None, transport=None):
    if not settings.image_analysis_enabled or not settings.image_analysis_groups:
        return "disabled"
    now = utc(now or datetime.now(UTC))
    async with AsyncSession(engine) as db:
        routes = (await db.scalars(select(GroupAnalysisSettings).where(
            GroupAnalysisSettings.analysis_mode.in_(["new", "shadow"]),
        ).order_by(GroupAnalysisSettings.group_id))).all()
        picked = None
        for candidate in routes:
            if not enabled(settings, candidate.group_id):
                continue
            route = await db.scalar(select(GroupAnalysisSettings).where(
                GroupAnalysisSettings.group_id == candidate.group_id,
            ).with_for_update(skip_locked=True))
            if route is None:
                continue
            group = await db.get(Group, route.group_id)
            if group.status != "active" or not group.pilot_authorized:
                continue
            await schedule_group(db, group, now)
            await db.flush()
            picked = await claim(db, group, route, settings, now)
            if picked:
                break
        await db.commit()
    if picked is None:
        return "idle"
    ident, token = picked
    async with AsyncSession(engine) as db:
        job = await db.get(ImageAnalysisJob, ident)
        raw = await db.get(RawMessage, job.raw_message_id)
        attachment = await db.get(MessageAttachment, job.attachment_id)
        file_id = attachment.telegram_file_id
    result, usage, failure, paid = None, None, None, False
    start = monotonic()
    try:
        image, mime = await download(settings.telegram_bot_token, file_id, transport=transport)
        if provider is None:
            from studgroup.ai import DeepSeekProvider
            provider = DeepSeekProvider(settings.deepseek_api_key.get_secret_value(),
                model=settings.deepseek_model, base_url=settings.deepseek_base_url)
        paid = True
        result, usage = await perform(provider, image, mime, job, raw)
    except DownloadError as error:
        failure = error
    except ProviderFailure as error:
        usage, failure = error.usage, error
    except Exception:  # noqa: BLE001 - durable boundary, sanitized reason only
        failure = DownloadError("invalid_image_response", False)
    async with AsyncSession(engine) as db:
        job = await db.scalar(select(ImageAnalysisJob).where(ImageAnalysisJob.id == ident).with_for_update())
        call = await db.scalar(select(ImageAnalysisCall).where(ImageAnalysisCall.claim_token == token).with_for_update())
        if job is None or call is None:
            return "expired"
        if job.claim_token != token or job.state != "running":
            return "superseded"
        charge_unknown = paid and not getattr(failure, "reservation_releasable", False)
        actual = usage_cost_usd(usage) if usage else call.reserved_usd if charge_unknown else Decimal(0)
        call.charged_usd, call.reserved_usd = actual, Decimal(0)
        call.state = "unknown" if usage is None and charge_unknown else "settled"
        call.latency_ms = int((monotonic() - start) * 1000)
        if usage:
            call.prompt_tokens, call.completion_tokens = usage.prompt_tokens, usage.completion_tokens
            call.cache_tokens = usage.prompt_cache_hit_tokens
        raw = await db.get(RawMessage, job.raw_message_id)
        attachment = await db.get(MessageAttachment, job.attachment_id)
        if (raw is None or attachment is None or not attachment.active
                or raw.revision != job.source_revision or utc(job.delete_at) <= now):
            job.state, job.error = "stale", "source_revision_changed"
        elif result is not None:
            job.result_json, job.state = result.model_dump_json(), "ready"
            job.pixel_sha256 = hashlib.sha256(image).hexdigest()
        else:
            job.error = failure.code
            if paid and usage is None and not getattr(failure, "reservation_releasable", False):
                job.state, job.error = "failed", "ambiguous_paid_attempt"
            elif failure.retryable and job.attempts < MAX_ATTEMPTS:
                job.state = "retry"
                job.available_at = now + timedelta(seconds=15 * 2 ** (job.attempts - 1))
            else:
                job.state = "failed"
        job.lease_until = None
        outcome = job.state
        await db.commit()
    return outcome
