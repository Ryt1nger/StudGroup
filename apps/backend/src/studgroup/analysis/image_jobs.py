"""Durable image worker. No publication imports; all paid calls follow committed reservations."""

import hashlib
import json
import re
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
from studgroup.analysis.photo_reading import MAX_IMAGE_BYTES, TELEGRAM_API, sniff_mime
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
    return (
        settings.image_analysis_enabled
        and settings.image_analysis_since is not None
        and str(group_id) in settings.image_analysis_groups
    )


def supported(attachment):
    return attachment.kind == "photo" or (
        attachment.kind == "document"
        and (
            (attachment.mime_type or "").lower().startswith("image/")
            or (attachment.file_name or "").lower().endswith((".png", ".jpg", ".jpeg", ".webp"))
        )
    )


def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def context_snapshot(db, raw):
    before = (
        await db.scalars(
            select(RawMessage)
            .where(
                RawMessage.group_id == raw.group_id,
                RawMessage.message_date <= raw.message_date,
                RawMessage.delete_at > datetime.now(UTC),
            )
            .order_by(RawMessage.message_date.desc(), RawMessage.telegram_message_id.desc())
            .limit(11)
        )
    ).all()
    after = (
        await db.scalars(
            select(RawMessage)
            .where(
                RawMessage.group_id == raw.group_id,
                RawMessage.message_date > raw.message_date,
                RawMessage.delete_at > datetime.now(UTC),
            )
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            .limit(8)
        )
    ).all()
    related = (
        await db.scalars(
            select(RawMessage)
            .where(
                RawMessage.group_id == raw.group_id,
                RawMessage.delete_at > datetime.now(UTC),
                or_(
                    RawMessage.telegram_message_id == raw.reply_to_message_id,
                    RawMessage.reply_to_message_id == raw.telegram_message_id,
                ),
            )
            .order_by(RawMessage.message_date)
            .limit(40)
        )
    ).all()
    rows = {row.id: row for row in [*before, raw, *after, *related]}
    context = {
        "messages": [
            {
                "id": row.telegram_message_id,
                "revision": row.revision,
                "text": row.text,
                "reply_to": row.reply_to_message_id,
                "topic": row.message_thread_id,
                "sent_at": utc(row.message_date).isoformat(),
            }
            for row in sorted(
                rows.values(), key=lambda row: (row.message_date, row.telegram_message_id)
            )
        ]
    }
    context["image_source_message_id"] = raw.telegram_message_id
    attachment_rows = (
        await db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.group_id == raw.group_id,
                MessageAttachment.raw_message_id.in_(rows),
                MessageAttachment.active.is_(True),
                MessageAttachment.delete_at > datetime.now(UTC),
            )
        )
    ).all()
    albums = {}
    for attachment in attachment_rows:
        if attachment.media_group_id:
            albums.setdefault(attachment.raw_message_id, set()).add(attachment.media_group_id)
    for item, row in zip(
        context["messages"],
        sorted(rows.values(), key=lambda row: (row.message_date, row.telegram_message_id)),
    ):
        item["media_group_ids"] = sorted(albums.get(row.id, ()))
    from studgroup.models import (
        CanonicalAcademicItem,
        CanonicalItemEvidence,
        Membership,
        SchedulePattern,
        User,
    )

    roles = dict(
        (
            await db.execute(
                select(User.telegram_user_id, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.group_id == raw.group_id, Membership.status == "active")
            )
        ).all()
    )
    for item, row in zip(
        context["messages"],
        sorted(rows.values(), key=lambda r: (r.message_date, r.telegram_message_id)),
    ):
        item["author_role"] = roles.get(row.sender_id)
    # Other empty pictures contribute no pixels/text to THIS visual pass. Keep source/reply
    # bridges, but do not invalidate paid context analysis merely because another file arrived.
    source_id = raw.telegram_message_id
    referenced = {
        item["reply_to"]
        for item in context["messages"]
        if item["reply_to"] is not None and (item["text"].strip() or item["id"] == source_id)
    }
    context["messages"] = [
        item
        for item in context["messages"]
        if item["id"] == source_id or item["text"].strip() or item["id"] in referenced
    ]
    group = await db.get(Group, raw.group_id)
    context["timezone"] = group.timezone
    context["schedule_version"] = group.schedule_version
    schedules = (
        await db.scalars(
            select(SchedulePattern)
            .where(
                SchedulePattern.group_id == raw.group_id,
            )
            .order_by(SchedulePattern.id)
            .limit(200)
        )
    ).all()
    context["schedule"] = [
        {
            "subject": row.subject,
            "weekday": row.weekday,
            "starts": row.starts.isoformat(),
            "ends": row.ends.isoformat(),
            "week": row.week,
            "teacher": row.teacher,
            "location": row.location,
            "valid_from": row.valid_from.isoformat(),
            "valid_until": row.valid_until.isoformat(),
        }
        for row in schedules
    ]
    anchor_ids = {raw.id, *[row.id for row in related]}
    linked_items = select(CanonicalItemEvidence.item_id).where(
        CanonicalItemEvidence.raw_message_id.in_(anchor_ids)
    )
    cards = (
        await db.scalars(
            select(CanonicalAcademicItem)
            .where(
                CanonicalAcademicItem.group_id == raw.group_id,
                CanonicalAcademicItem.state != "cancelled",
                CanonicalAcademicItem.id.in_(linked_items),
            )
            .order_by(CanonicalAcademicItem.updated_at.desc())
            .limit(30)
        )
    ).all()
    context["existing_items"] = [
        {
            "id": str(row.id),
            "revision": row.revision,
            "subject": row.subject,
            "title": row.title,
            "description": row.description,
        }
        for row in cards
    ]
    from studgroup.analysis.subject_catalog import load_subject_catalog
    from studgroup.models import GroupEvent, GroupEventSource, GroupKnowledgeFact

    known = (
        await db.scalars(
            select(GroupKnowledgeFact).where(GroupKnowledgeFact.group_id == raw.group_id)
        )
    ).all()
    catalog = await load_subject_catalog(db, raw.group_id, known)
    context["subject_catalog"] = catalog.model_dump(mode="json") if catalog is not None else None
    pixel_reading = await db.scalar(
        select(ImageAnalysisJob.result_json)
        .where(
            ImageAnalysisJob.raw_message_id == raw.id,
            ImageAnalysisJob.source_revision == raw.revision,
            ImageAnalysisJob.pass_number == 1,
            ImageAnalysisJob.state == "ready",
        )
        .order_by(ImageAnalysisJob.created_at.desc())
        .limit(1)
    )
    pixel_text = (
        VisualReading.model_validate_json(pixel_reading).extracted_text if pixel_reading else ""
    )
    normalize = lambda text: " ".join(text.casefold().replace("ё", "е").split())
    relevant_text = normalize(
        "\n".join([pixel_text, *[item["text"] for item in context["messages"]]])
    )

    def named_in_pixels(label):
        label = normalize(label)
        return (
            len(label) >= 3
            and re.search(r"(?<!\w)" + re.escape(label) + r"(?!\w)", normalize(pixel_text))
            is not None
        )

    named_subjects = {
        normalize(row["subject"]) for row in context["schedule"] if named_in_pixels(row["subject"])
    }
    if catalog:
        for subject in catalog.subjects:
            if any(
                named_in_pixels(label)
                for label in [subject.name, *subject.aliases, *subject.teachers]
                if label.strip()
            ):
                named_subjects.add(normalize(subject.name))
    context["schedule_complete"] = not bool(named_subjects)
    if named_subjects:
        context["schedule"] = [
            row for row in context["schedule"] if normalize(row["subject"]) in named_subjects
        ]
    linked_events = set(
        (
            await db.scalars(
                select(GroupEventSource.event_id).where(
                    GroupEventSource.raw_message_id.in_(anchor_ids)
                )
            )
        ).all()
    )
    events = (
        await db.scalars(
            select(GroupEvent)
            .where(
                GroupEvent.group_id == raw.group_id,
                GroupEvent.delete_at > datetime.now(UTC),
            )
            .order_by(GroupEvent.created_at.desc())
            .limit(50)
        )
    ).all()
    context["existing_events"] = [
        {
            "id": str(event.id),
            "revision": event.revision,
            "state": event.state,
            **json.loads(event.payload_json),
        }
        for event in events
        if event.id in linked_events or event.title_key and event.title_key in relevant_text
    ]
    return context


async def schedule_group(db, group, now, since, model_key="", provider_identity=None):
    """Caller holds group settings lock. Enumerate every retained image, not just six."""
    attachments = (
        await db.scalars(
            select(MessageAttachment)
            .join(RawMessage, RawMessage.id == MessageAttachment.raw_message_id)
            .where(
                MessageAttachment.group_id == group.id,
                MessageAttachment.active.is_(True),
                MessageAttachment.delete_at > now,
                RawMessage.group_id == group.id,
                RawMessage.delete_at > now,
                RawMessage.message_date >= since,
                RawMessage.message_date >= (group.bot_added_at or now - timedelta(days=30)),
            )
            .order_by(MessageAttachment.created_at, MessageAttachment.id)
        )
    ).all()
    from studgroup.analysis.image_reading import PASS_A_VERSION, PASS_B_VERSION

    created = 0
    for attachment in attachments:
        if not supported(attachment):
            continue
        raw = await db.get(RawMessage, attachment.raw_message_id)
        key = digest(
            {
                "file": attachment.stable_file_id,
                "revision": raw.revision,
                "version": IMAGE_VERSION,
                "prompt": PASS_A_VERSION,
                "model_key": model_key,
            }
        )
        jobs = (
            await db.scalars(
                select(ImageAnalysisJob).where(
                    ImageAnalysisJob.attachment_id == attachment.id,
                )
            )
        ).all()
        for job in jobs:
            expected_version = (
                f"{IMAGE_VERSION}/{PASS_A_VERSION if job.pass_number == 1 else PASS_B_VERSION}"
            )
            if (
                job.source_revision != raw.revision
                or job.version != expected_version
                or job.model_key != model_key
            ) and job.state != "stale":
                job.state, job.error = "stale", "source_or_prompt_revision_changed"
        first = next(
            (job for job in jobs if job.context_sha256 == key and job.pass_number == 1), None
        )
        if first is not None:
            restore_successful_snapshot(first, now)
            if first.state != "ready":
                continue
            context = await context_snapshot(db, raw)
            context["pass_a_sha256"] = digest(first.result_json)
            context["pass_version"] = PASS_B_VERSION
            context["model_key"] = model_key
            context["provider_identity"] = provider_identity or {}
            # Published-card revisions must not make image analysis feed back into itself.
            second_key = digest(
                {
                    key: value
                    for key, value in context.items()
                    if key not in {"existing_items", "existing_events"}
                }
            )
            for job in jobs:
                if (
                    job.pass_number == 2
                    and job.context_sha256 != second_key
                    and job.state != "stale"
                ):
                    job.state, job.error = "stale", "context_revision_changed"
            previous = next(
                (job for job in jobs if job.pass_number == 2 and job.context_sha256 == second_key),
                None,
            )
            if previous is not None:
                restore_successful_snapshot(previous, now)
            else:
                db.add(
                    ImageAnalysisJob(
                        group_id=group.id,
                        attachment_id=attachment.id,
                        raw_message_id=raw.id,
                        source_revision=raw.revision,
                        context_sha256=second_key,
                        context_json=json.dumps(context, ensure_ascii=False),
                        version=f"{IMAGE_VERSION}/{PASS_B_VERSION}",
                        model_key=model_key,
                        pass_number=2,
                        state="pending",
                        attempts=0,
                        available_at=now,
                        created_at=now,
                        delete_at=min(raw.delete_at, attachment.delete_at),
                    )
                )
                created += 1
            continue
        db.add(
            ImageAnalysisJob(
                group_id=group.id,
                attachment_id=attachment.id,
                raw_message_id=raw.id,
                source_revision=raw.revision,
                context_sha256=key,
                context_json=json.dumps({"provider_identity": provider_identity or {}}),
                version=f"{IMAGE_VERSION}/{PASS_A_VERSION}",
                model_key=model_key,
                pass_number=1,
                state="pending",
                attempts=0,
                available_at=now,
                created_at=now,
                delete_at=min(raw.delete_at, attachment.delete_at),
            )
        )
        created += 1
    return created


def restore_successful_snapshot(job, now):
    """A previously successful exact input may become current again; never retry a failure."""
    if (
        job.state == "stale"
        and job.result_json is not None
        and utc(job.delete_at) > now
        and VisualReading.model_validate_json(job.result_json).complete
    ):
        job.state, job.error = "ready", None


class DownloadError(Exception):
    def __init__(self, code, retryable):
        self.code, self.retryable = code, retryable


async def download(token, file_id, *, transport=None):
    """Bound streamed bytes, validate Telegram file path, retain retryable/terminal reasons."""
    if not token:
        raise DownloadError("telegram_token_missing", False)
    try:
        async with httpx.AsyncClient(
            timeout=30, follow_redirects=False, trust_env=False, transport=transport
        ) as client:
            info = await client.post(
                f"{TELEGRAM_API}/bot{token}/getFile", json={"file_id": file_id}
            )
            if info.status_code != 200:
                raise DownloadError(
                    "telegram_get_file", info.status_code == 429 or info.status_code >= 500
                )
            result = info.json().get("result") or {}
            path = result.get("file_path")
            if (
                not isinstance(path, str)
                or not path
                or ".." in PurePosixPath(path).parts
                or path.startswith("/")
                or ":" in path
                or "?" in path
                or "#" in path
            ):
                raise DownloadError("telegram_invalid_path", False)
            if int(result.get("file_size") or 0) > MAX_IMAGE_BYTES:
                raise DownloadError("image_too_large", False)
            content = bytearray()
            async with client.stream("GET", f"{TELEGRAM_API}/file/bot{token}/{path}") as response:
                if response.status_code != 200:
                    raise DownloadError(
                        "telegram_download",
                        response.status_code == 429 or response.status_code >= 500,
                    )
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


def provider_identity(settings):
    return {
        "model": settings.deepseek_model,
        "revision": settings.image_model_revision,
        "schema": IMAGE_VERSION,
    }


def model_identity(settings):
    return digest(provider_identity(settings))


async def reusable_reading(db, job, now, *, stable_file_id=None, pixels=None):
    current_attachment = await db.get(MessageAttachment, job.attachment_id)
    query = (
        select(ImageAnalysisJob)
        .join(MessageAttachment, MessageAttachment.id == ImageAnalysisJob.attachment_id)
        .join(RawMessage, RawMessage.id == ImageAnalysisJob.raw_message_id)
        .where(
            ImageAnalysisJob.group_id == job.group_id,
            ImageAnalysisJob.id != job.id,
            ImageAnalysisJob.pass_number == 1,
            or_(
                (ImageAnalysisJob.state == "ready")
                & (RawMessage.revision == ImageAnalysisJob.source_revision)
                & MessageAttachment.active.is_(True),
                (ImageAnalysisJob.state == "stale")
                & (ImageAnalysisJob.error == "source_or_prompt_revision_changed")
                & (RawMessage.id == job.raw_message_id)
                & (RawMessage.revision > ImageAnalysisJob.source_revision)
                & (MessageAttachment.stable_file_id == current_attachment.stable_file_id),
            ),
            ImageAnalysisJob.version == job.version,
            ImageAnalysisJob.model_key == job.model_key,
            ImageAnalysisJob.delete_at > now,
            RawMessage.delete_at > now,
            RawMessage.group_id == job.group_id,
            MessageAttachment.group_id == job.group_id,
            MessageAttachment.delete_at > now,
        )
    )
    if pixels is not None:
        query = query.where(ImageAnalysisJob.pixel_sha256 == pixels)
    else:
        query = query.where(MessageAttachment.stable_file_id == stable_file_id)
    return await db.scalar(query.order_by(ImageAnalysisJob.created_at.desc()).limit(1))


def reuse_reading(job, source):
    job.result_json, job.pixel_sha256 = source.result_json, source.pixel_sha256
    job.visual_hash, job.visual_coverage_json = source.visual_hash, source.visual_coverage_json
    job.state, job.error, job.reused_from_job_id = "ready", None, source.id
    # Cache never prolongs the lifetime of an older private derivative.
    job.delete_at = min(utc(job.delete_at), utc(source.delete_at))


async def total(db, model, column, *filters):
    return Decimal(
        await db.scalar(
            select(func.coalesce(func.sum(column), 0)).select_from(model).where(*filters)
        )
        or 0
    )


async def claim(db, group, route, settings, now):
    job = await db.scalar(
        select(ImageAnalysisJob)
        .where(
            ImageAnalysisJob.group_id == group.id,
            ImageAnalysisJob.delete_at > now,
            or_(
                ImageAnalysisJob.state.in_(["pending", "retry", "budget_deferred"])
                & (ImageAnalysisJob.available_at <= now),
                (ImageAnalysisJob.state == "running") & (ImageAnalysisJob.lease_until <= now),
            ),
        )
        .order_by(ImageAnalysisJob.created_at, ImageAnalysisJob.pass_number, ImageAnalysisJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    if job.state == "running":
        # Unknown external outcome: do not silently purchase it again.
        call = await db.scalar(
            select(ImageAnalysisCall).where(ImageAnalysisCall.claim_token == job.claim_token)
        )
        if call is not None and call.state == "reserved":
            call.state, call.charged_usd = "unknown", call.reserved_usd
            call.reserved_usd = Decimal(0)
        job.state, job.error, job.lease_until = "failed", "ambiguous_paid_attempt", None
        return None
    raw = await db.get(RawMessage, job.raw_message_id)
    attachment = await db.get(MessageAttachment, job.attachment_id)
    if (
        raw is None
        or attachment is None
        or not attachment.active
        or raw.revision != job.source_revision
        or utc(raw.delete_at) <= now
        or utc(attachment.delete_at) <= now
    ):
        job.state, job.error = "stale", "source_revision_changed"
        return None
    if job.pass_number == 1 and settings.image_model_revision:
        cached = await reusable_reading(db, job, now, stable_file_id=attachment.stable_file_id)
        if cached is not None:
            reuse_reading(job, cached)
            return job.id, None  # no reservation and no paid attempt, even at exhausted budget
    reserve = Decimal(str(settings.image_pass_reservation_usd))
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    group_filter = ImageAnalysisCall.group_id == group.id
    committed = await total(
        db,
        ImageAnalysisCall,
        ImageAnalysisCall.charged_usd + ImageAnalysisCall.reserved_usd,
        group_filter,
        ImageAnalysisCall.created_at >= day_start,
    )
    text_spent = await total(
        db,
        ShadowEvaluationRun,
        ShadowEvaluationRun.charged_usd + ShadowEvaluationRun.reserved_usd,
        ShadowEvaluationRun.group_id == group.id,
        ShadowEvaluationRun.created_at >= day_start,
    )
    image_jobs = select(ImageAnalysisJob.id).where(ImageAnalysisJob.attachment_id == attachment.id)
    image_spent = await total(
        db,
        ImageAnalysisCall,
        ImageAnalysisCall.charged_usd + ImageAnalysisCall.reserved_usd,
        ImageAnalysisCall.job_id.in_(image_jobs),
    )
    if reserve <= 0 or committed + text_spent + reserve > Decimal(route.shadow_daily_budget_usd):
        job.state, job.error = "budget_deferred", "image_group_budget"
        job.available_at = day_start + timedelta(days=1)
        return None
    if image_spent + reserve > Decimal(str(settings.image_total_budget_usd)):
        job.state, job.error = "budget_deferred", "image_budget_exhausted"
        job.available_at = day_start + timedelta(days=1)
        return None
    hour = now - timedelta(hours=1)
    hourly = await total(
        db,
        ImageAnalysisCall,
        ImageAnalysisCall.charged_usd + ImageAnalysisCall.reserved_usd,
        group_filter,
        ImageAnalysisCall.created_at >= hour,
    )
    hourly += await total(
        db,
        ShadowEvaluationRun,
        ShadowEvaluationRun.charged_usd + ShadowEvaluationRun.reserved_usd,
        ShadowEvaluationRun.group_id == group.id,
        ShadowEvaluationRun.created_at >= hour,
    )
    # An image batch is one Telegram album, or one non-album message.
    batch = select(MessageAttachment.id).where(MessageAttachment.group_id == group.id)
    if attachment.media_group_id:
        batch = batch.where(MessageAttachment.media_group_id == attachment.media_group_id)
    else:
        batch = batch.where(MessageAttachment.raw_message_id == attachment.raw_message_id)
    batch_jobs = select(ImageAnalysisJob.id).where(ImageAnalysisJob.attachment_id.in_(batch))
    batch_spent = await total(
        db,
        ImageAnalysisCall,
        ImageAnalysisCall.charged_usd + ImageAnalysisCall.reserved_usd,
        ImageAnalysisCall.job_id.in_(batch_jobs),
    )
    if hourly + reserve > Decimal(str(settings.image_hourly_budget_usd)):
        job.state, job.error = "budget_deferred", "image_hourly_budget"
        job.available_at = now + timedelta(minutes=10)
        return None
    if batch_spent + reserve > Decimal(str(settings.image_package_budget_usd)):
        job.state, job.error = "budget_deferred", "image_package_budget_exhausted"
        job.available_at = day_start + timedelta(days=1)
        return None
    token = uuid.uuid4()
    job.state, job.claim_token = "running", token
    job.attempts += 1
    job.lease_until = now + LEASE
    job.error = None
    db.add(
        ImageAnalysisCall(
            job_id=job.id,
            group_id=group.id,
            claim_token=token,
            state="reserved",
            reserved_usd=reserve,
            charged_usd=0,
            created_at=now,
        )
    )
    return job.id, token


async def perform(provider, image, mime, job, raw, first=None):
    from studgroup.analysis.image_reading import read_pass

    return await read_pass(
        provider,
        image,
        mime,
        job.pass_number,
        context={
            key: value
            for key, value in json.loads(job.context_json).items()
            if key not in {"provider_identity", "model_key"}
        },
        first=first,
    )


async def process_images_next(engine, settings, provider=None, now=None, transport=None):
    if (
        not settings.image_analysis_enabled
        or not settings.image_analysis_groups
        or settings.image_analysis_since is None
    ):
        return "disabled"
    now = utc(now or datetime.now(UTC))
    async with AsyncSession(engine) as db:
        routes = (
            await db.scalars(
                select(GroupAnalysisSettings)
                .where(
                    GroupAnalysisSettings.analysis_mode.in_(["new", "shadow"]),
                )
                .order_by(GroupAnalysisSettings.group_id)
            )
        ).all()
        picked = None
        for candidate in routes:
            if not enabled(settings, candidate.group_id):
                continue
            route = await db.scalar(
                select(GroupAnalysisSettings)
                .where(
                    GroupAnalysisSettings.group_id == candidate.group_id,
                )
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if route is None or route.analysis_mode not in {"new", "shadow"}:
                continue
            group = await db.get(Group, route.group_id)
            if group.status != "active" or not group.pilot_authorized:
                continue
            await schedule_group(
                db,
                group,
                now,
                settings.image_analysis_since,
                model_identity(settings),
                provider_identity(settings),
            )
            await db.flush()
            picked = await claim(db, group, route, settings, now)
            if picked:
                break
        await db.commit()
    if picked is None:
        return "idle"
    ident, token = picked
    if token is None:
        return "reused"
    async with AsyncSession(engine) as db:
        job = await db.get(ImageAnalysisJob, ident)
        raw = await db.get(RawMessage, job.raw_message_id)
        attachment = await db.get(MessageAttachment, job.attachment_id)
        file_id = attachment.telegram_file_id
        first = None
        from studgroup.analysis.image_reading import PASS_A_VERSION

        if job.pass_number == 2:
            first_job = await db.scalar(
                select(ImageAnalysisJob).where(
                    ImageAnalysisJob.attachment_id == job.attachment_id,
                    ImageAnalysisJob.source_revision == job.source_revision,
                    ImageAnalysisJob.pass_number == 1,
                    ImageAnalysisJob.state == "ready",
                    ImageAnalysisJob.version == f"{IMAGE_VERSION}/{PASS_A_VERSION}",
                    ImageAnalysisJob.model_key == job.model_key,
                )
            )
            first = VisualReading.model_validate_json(first_job.result_json) if first_job else None
    result, usage, failure, paid, cached = None, None, None, False, None
    start = monotonic()
    try:
        image, mime = await download(settings.telegram_bot_token, file_id, transport=transport)
        if provider is None:
            from studgroup.ai import DeepSeekProvider

            provider = DeepSeekProvider(
                settings.deepseek_api_key.get_secret_value(),
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
            )
        if job.pass_number == 2 and first is None:
            raise DownloadError("pass_a_missing", False)
        if job.pass_number == 2 and first_job.pixel_sha256 != hashlib.sha256(image).hexdigest():
            raise DownloadError("image_pixels_changed", False)
        if job.pass_number == 1 and settings.image_model_revision:
            async with AsyncSession(engine) as cache_db:
                cached = await reusable_reading(
                    cache_db, job, now, pixels=hashlib.sha256(image).hexdigest()
                )
        if cached is not None:
            result = VisualReading.model_validate_json(cached.result_json)
        else:
            paid = True
            result, usage = await perform(provider, image, mime, job, raw, first)
    except DownloadError as error:
        failure = error
    except ProviderFailure as error:
        usage, failure = error.usage, error
    except Exception:  # noqa: BLE001 - durable boundary, sanitized reason only
        failure = DownloadError("invalid_image_response", False)
    async with AsyncSession(engine) as db:
        job = await db.scalar(
            select(ImageAnalysisJob).where(ImageAnalysisJob.id == ident).with_for_update()
        )
        call = await db.scalar(
            select(ImageAnalysisCall)
            .where(ImageAnalysisCall.claim_token == token)
            .with_for_update()
        )
        if job is None or call is None:
            return "expired"
        superseded = job.claim_token != token or job.state != "running"
        charge_unknown = paid and not getattr(failure, "reservation_releasable", False)
        actual = (
            usage_cost_usd(usage) if usage else call.reserved_usd if charge_unknown else Decimal(0)
        )
        call.charged_usd, call.reserved_usd = actual, Decimal(0)
        call.state = "unknown" if usage is None and charge_unknown else "settled"
        call.latency_ms = int((monotonic() - start) * 1000)
        if usage:
            call.prompt_tokens, call.completion_tokens = (
                usage.prompt_tokens,
                usage.completion_tokens,
            )
            call.cache_tokens = usage.prompt_cache_hit_tokens
        if superseded:
            await db.commit()  # even a stale pass must retain the actual paid usage
            return "superseded"
        raw = await db.get(RawMessage, job.raw_message_id)
        attachment = await db.get(MessageAttachment, job.attachment_id)
        context_changed = False
        if raw is not None and job.pass_number == 2:
            current = await context_snapshot(db, raw)
            from studgroup.analysis.image_reading import PASS_B_VERSION

            current["pass_a_sha256"] = digest(first_job.result_json)
            current["pass_version"] = PASS_B_VERSION
            current["model_key"] = job.model_key
            current["provider_identity"] = json.loads(job.context_json).get("provider_identity", {})
            context_changed = (
                digest(
                    {
                        k: v
                        for k, v in current.items()
                        if k not in {"existing_items", "existing_events"}
                    }
                )
                != job.context_sha256
            )
        if (
            raw is None
            or attachment is None
            or not attachment.active
            or raw.revision != job.source_revision
            or utc(job.delete_at) <= utc(datetime.now(UTC))
            or context_changed
        ):
            job.state, job.error = "stale", "context_or_source_revision_changed"
        elif actual > Decimal(str(settings.image_pass_reservation_usd)):
            job.state, job.error = "failed", "image_charge_over_reservation"
        elif result is not None and cached is not None:
            reuse_reading(job, cached)
            call.state = "reused"
        elif result is not None:
            job.result_json = result.model_dump_json()
            job.state = "ready" if result.complete else "failed"
            job.error = None if result.complete else "image_reading_incomplete"
            job.pixel_sha256 = hashlib.sha256(image).hexdigest()
            from studgroup.photo_dedupe import visual_fingerprint

            job.visual_hash = visual_fingerprint(image)
            from studgroup.image_display_coverage import build_coverage

            coverage = build_coverage(image, result)
            if first is not None and " ".join(first.extracted_text.split()) != " ".join(
                result.extracted_text.split()
            ):
                coverage = None  # conflicting pixel transcriptions cannot justify hiding a source
            job.visual_coverage_json = json.dumps(coverage) if coverage else None
        else:
            job.error = failure.code
            if paid and usage is None and not getattr(failure, "reservation_releasable", False):
                job.state, job.error = "failed", "ambiguous_paid_attempt"
            elif failure.retryable and job.attempts < MAX_ATTEMPTS:
                job.state = "retry"
                job.available_at = now + timedelta(seconds=15 * 2 ** (job.attempts - 1))
            else:
                job.state = "failed"
        if job.state == "ready" and job.pass_number == 2:
            from studgroup.analysis.group_events import capture_image
            from studgroup.analysis.image_schedule import capture

            await capture(db, job, result, now)
            await capture_image(db, job, result, raw, now)
        job.lease_until = None
        if (
            settings.image_analysis_mode == "live"
            and job.state in {"ready", "failed"}
            and (job.pass_number == 2 or job.state == "failed")
            and raw is not None
            and raw.processing_state in {"completed", "image_pending", "needs_context"}
            and raw.revision == job.source_revision
        ):
            raw.processing_state = "pending"
            raw.analysis_generation += 1
        outcome = job.state
        await db.commit()
    return outcome
