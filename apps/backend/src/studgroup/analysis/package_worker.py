"""Live package writer: the new engine's worker for groups whose analysis mode is ``new``.

One iteration plans at most one package (a contiguous window of the chat with up to 24 target
messages plus read-only context) and stores it as a durable ``ShadowEvaluationRun`` row without a
raw message (package runs and per-message shadow runs never pick each other's rows). The run is
then leased, analysed in two passes by ``run_shadow`` under a budget reservation, and - in the SAME
transaction that stores the result - its accepted proposals go through the canonical resolver and
writer (``promote_run``) with the group's timetable subjects and intelligent-deadline context.
The target messages are marked ``completed`` only in that transaction, so a crash or a lost lease
re-runs the package and never publishes twice (idempotent resolver, one open run per group).

The legacy engine never touches ``new`` groups (``processing.claim`` excludes them), and this
worker never touches legacy/shadow groups: exactly one writer per group.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import photo_dedupe
from studgroup.ai import ProviderFailure
from studgroup.ai_schedule import window
from studgroup.analysis import duplicate_guard, photo_reading
from studgroup.analysis.backfill import promote_run
from studgroup.analysis.context import assemble_memory_context
from studgroup.analysis.contracts import AnalysisInput, SourceMessage
from studgroup.analysis.package_run import DEFAULT_MAX_TARGETS, plan_packages
from studgroup.analysis.shadow import (
    ShadowPassFailure,
    failure_stage,
    run_shadow,
    usage_cost_usd,
)
from studgroup.analysis.shadow_worker import (
    SHADOW_MAX_ATTEMPTS,
    SHADOW_RESERVATION_USD,
    _claim,
    _metrics,
    _utc,
    attempt_base,
)
from studgroup.analysis.subject_catalog import catalog_summary, load_subject_catalog
from studgroup.analysis.subject_resolver import Lesson
from studgroup.deadline_context import build_deadline_context
from studgroup.models import (
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    Group,
    GroupAnalysisSettings,
    GroupKnowledgeFact,
    GroupTopic,
    Membership,
    MessageAttachment,
    RawMessage,
    SchedulePattern,
    ShadowEvaluationRun,
    User,
)
from studgroup.owner_updates import queue as queue_owner_message
from studgroup.processing import incident, incident_once

OPEN_STATES = ("queued", "running", "retry", "budget_deferred")
CONTEXT_BEFORE = 10  # read-only messages before the first target
CONTEXT_AFTER = 8  # read-only messages after the last target (if they exist yet)
RUN_RETENTION = timedelta(days=30)
EXISTING_ITEMS_LIMIT = 30  # active cards offered to the model as update/cancel targets
EXISTING_ITEMS_WINDOW = timedelta(days=30)


def _gate(settings, now):
    if not settings.new_engine_enabled:
        return False
    until = settings.new_engine_enabled_until
    return until is None or _utc(until) > now


def _source_message(row, *, roles, topics, attachments):
    items = attachments.get(row.id, [])
    return SourceMessage(
        telegram_message_id=row.telegram_message_id,
        revision=row.revision,
        message_date=_utc(row.message_date),
        # What the vision pass read on a photo is part of the message for both model passes, the
        # card editor and the deadline policy; the stored RawMessage text is never changed.
        text=photo_reading.enriched_text(row.text, items),
        reply_to_message_id=row.reply_to_message_id,
        topic_id=row.message_thread_id,
        topic_name=topics.get(row.message_thread_id),
        author_alias=(
            f"member-{uuid.uuid5(row.group_id, str(row.sender_id)).hex[:12]}"
            if row.sender_id is not None
            else None
        ),
        author_role=roles.get(row.sender_id),
        attachments=[
            {key: value for key, value in item.items() if not key.startswith("photo_")}
            for item in items
        ],
    )


async def _source_messages(db, group, rows):
    ids = [row.id for row in rows]
    attachments = {}
    stored = (
        await db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.raw_message_id.in_(ids), MessageAttachment.active.is_(True)
            )
        )
    ).all()
    # Two shots of one board (wide, then close-up) are one fact: the model reads the better one
    # only, so the second shot is not mistaken for a supplement that changes the card.
    kept = {a.id for a in photo_dedupe.distinct(stored)}
    for attachment in stored:
        attachments.setdefault(attachment.raw_message_id, []).append(
            {
                "kind": attachment.kind,
                "mime_type": attachment.mime_type,
                "size_bytes": attachment.size_bytes,
                "media_group_id": attachment.media_group_id,
                "processing_state": (
                    attachment.processing_state if attachment.id in kept else "irrelevant"
                ),
                "file_name": (attachment.file_name or None) and attachment.file_name[:255],
                "photo_summary": attachment.photo_summary,
                "photo_text": attachment.photo_text,
            }
        )
    topics = {
        row.telegram_thread_id: row.name
        for row in (
            await db.scalars(select(GroupTopic).where(GroupTopic.group_id == group.id))
        ).all()
    }
    senders = {row.sender_id for row in rows if row.sender_id is not None}
    roles = {}
    if senders:
        for telegram_user_id, role in (
            await db.execute(
                select(User.telegram_user_id, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    Membership.group_id == group.id,
                    Membership.status == "active",
                    User.telegram_user_id.in_(senders),
                )
            )
        ).all():
            roles[telegram_user_id] = role
    return [
        _source_message(row, roles=roles, topics=topics, attachments=attachments) for row in rows
    ]


def _eligible(group, settings, now):
    """Filters for pending targets; with the hourly schedule only messages before the slot."""
    filters = [
        RawMessage.group_id == group.id,
        RawMessage.processing_state == "pending",
        RawMessage.imported.is_(False),
        RawMessage.delete_at > now,
    ]
    if settings.ai_schedule_enabled:
        timing = window(now, settings.ai_schedule_timezone)
        if timing is None:
            return None
        filters += [
            RawMessage.live_received_at.is_not(None),
            RawMessage.live_received_at <= timing[0],
        ]
    if group.bot_added_at is not None:
        filters.append(RawMessage.message_date >= group.bot_added_at)
    return filters


async def _read_pending_photos(db, group, rows, target_ids, settings, provider, transport, now):
    """Read the unread photos of these messages; returns (cost in USD, retry_later).

    One try per photo: a photo that cannot be downloaded or read is marked ``failed`` and the
    package goes on without it. A temporary provider fault (rate limit, network) returns
    ``retry_later`` so the package is not frozen without a photo that would be readable a minute
    later. Cost is returned so the package run carries it in the budget.
    """
    if not settings.photo_reading_enabled or not rows:
        return Decimal(0), False
    pending = (
        await db.scalars(
            select(MessageAttachment)
            .where(
                MessageAttachment.raw_message_id.in_([row.id for row in rows]),
                MessageAttachment.kind == "photo",
                MessageAttachment.active.is_(True),
                MessageAttachment.processing_state == "metadata_only",
            )
            .order_by(MessageAttachment.created_at, MessageAttachment.id)
            .limit(photo_reading.MAX_PHOTOS_PER_PACKAGE * 8)
        )
    ).all()
    # The photos of the package's own targets come first; context photos only use what is left.
    pending = sorted(pending, key=lambda item: item.raw_message_id not in target_ids)[
        : photo_reading.MAX_PHOTOS_PER_PACKAGE
    ]
    if not pending:
        return Decimal(0), False
    if provider is None:
        from studgroup.ai import DeepSeekProvider

        provider = DeepSeekProvider(
            settings.deepseek_api_key.get_secret_value(),
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
        )
    captions = {row.id: row.text for row in rows}
    cost = Decimal(0)
    for attachment in pending:
        attachment.read_at = now
        image = await photo_reading.download_telegram_file(
            settings.telegram_bot_token, attachment.telegram_file_id, transport=transport
        )
        mime = photo_reading.sniff_mime(image) if image else None
        if mime is None:
            attachment.processing_state = "failed"
            continue
        try:
            reading = await photo_reading.read_photo(
                provider, image, mime, captions.get(attachment.raw_message_id, "")
            )
        except ProviderFailure as error:
            if error.usage is not None:
                cost += usage_cost_usd(error.usage)
            if error.retryable:
                attachment.read_at = None
                return cost, True
            attachment.processing_state = "failed"
            continue
        cost += usage_cost_usd(reading.usage)
        attachment.photo_summary = reading.summary or None
        attachment.photo_text = reading.text or None
        attachment.processing_state = "irrelevant" if reading.relevance == "irrelevant" else "ready"
    return cost, False


async def _plan_next(db, group, route, settings, now, provider=None, transport=None):
    filters = _eligible(group, settings, now)
    if filters is None:
        return None
    pending = (
        await db.scalars(
            select(RawMessage)
            .where(*filters)
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            .limit(DEFAULT_MAX_TARGETS * 2)
        )
    ).all()
    if not pending:
        return None
    revision = pending[0].revision
    targets = [row for row in pending if row.revision == revision][:DEFAULT_MAX_TARGETS]
    first, last = targets[0].message_date, targets[-1].message_date
    live = [RawMessage.group_id == group.id, RawMessage.delete_at > now]
    if group.bot_added_at is not None:
        live.append(RawMessage.message_date >= group.bot_added_at)
    before = (
        await db.scalars(
            select(RawMessage)
            .where(*live, RawMessage.message_date < first)
            .order_by(RawMessage.message_date.desc(), RawMessage.telegram_message_id.desc())
            .limit(CONTEXT_BEFORE)
        )
    ).all()
    middle = (
        await db.scalars(
            select(RawMessage).where(
                *live, RawMessage.message_date >= first, RawMessage.message_date <= last
            )
        )
    ).all()
    after = (
        await db.scalars(
            select(RawMessage)
            .where(*live, RawMessage.message_date > last)
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            .limit(CONTEXT_AFTER)
        )
    ).all()
    rows = {row.id: row for row in [*before, *middle, *after]}
    target_row_ids = {row.id for row in targets}
    photo_cost, retry_later = await _read_pending_photos(
        db, group, list(rows.values()), target_row_ids, settings, provider, transport, now
    )
    if retry_later:
        await db.commit()  # keep the cost and the photos already read
        return "photo_retry"
    messages = await _source_messages(db, group, list(rows.values()))
    facts = (
        await db.scalars(select(GroupKnowledgeFact).where(GroupKnowledgeFact.group_id == group.id))
    ).all()
    catalog = await load_subject_catalog(db, group.id, facts)
    packages = plan_packages(
        messages,
        group_id=group.id,
        timezone=group.timezone,
        target_ids=[row.telegram_message_id for row in targets],
        source_revision=revision,
        subject_catalog=catalog,
    )
    package = packages[0]
    # A reopened window (owner re-analysis) bumps the generation, so the same messages get a new
    # run key instead of being treated as the package that already ran.
    generation = max(
        row.analysis_generation for row in targets if row.telegram_message_id in package.target_ids
    )
    run_key = hashlib.sha256(f"{package.key}:{generation}".encode()).hexdigest()
    memory = assemble_memory_context(group.id, " ".join(row.text for row in messages), facts)
    return package, memory, revision, run_key, generation, photo_cost


async def reopen_unread_photo_messages(db, group, since, now):
    """Queue stored messages whose photos were never read for one more package analysis.

    Only RawMessage state and generation change (the same effect as the owner's ``reopen``), and
    only for messages that carry a photo still ``metadata_only``. Once the package has read the
    photo its state is no longer ``metadata_only``, so the message is never reopened again: the
    call is idempotent and safe to repeat on every iteration. No card is touched here.
    """
    lower = _utc(since)
    if group.bot_added_at is not None:
        lower = max(lower, _utc(group.bot_added_at))
    unread = (
        select(MessageAttachment.raw_message_id)
        .where(
            MessageAttachment.kind == "photo",
            MessageAttachment.active.is_(True),
            MessageAttachment.processing_state == "metadata_only",
        )
        .scalar_subquery()
    )
    result = await db.execute(
        update(RawMessage)
        .where(
            RawMessage.group_id == group.id,
            RawMessage.imported.is_(False),
            RawMessage.message_date >= lower,
            RawMessage.delete_at > now,
            RawMessage.processing_state.in_(["completed", "failed", "needs_context"]),
            RawMessage.id.in_(unread),
        )
        .values(processing_state="pending", analysis_generation=RawMessage.analysis_generation + 1)
    )
    return result.rowcount or 0


async def enqueue_next_package(engine, settings, now=None, provider=None, transport=None):
    """Create the next package run of one ``new`` group; at most one open run per group."""
    now = _utc(now or datetime.now(UTC))
    async with AsyncSession(engine) as db:
        groups = (
            await db.execute(
                select(Group, GroupAnalysisSettings)
                .join(GroupAnalysisSettings, GroupAnalysisSettings.group_id == Group.id)
                .where(
                    GroupAnalysisSettings.analysis_mode == "new",
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                )
                .order_by(Group.id)
            )
        ).all()
        for group, _route in groups:
            # The settings-row lock serializes enqueue/reserve decisions for this group.
            route = await db.scalar(
                select(GroupAnalysisSettings)
                .where(GroupAnalysisSettings.group_id == group.id)
                .with_for_update(skip_locked=True)
            )
            if route is None:
                continue
            open_run = await db.scalar(
                select(ShadowEvaluationRun.id).where(
                    ShadowEvaluationRun.group_id == group.id,
                    ShadowEvaluationRun.raw_message_id.is_(None),
                    ShadowEvaluationRun.state.in_(OPEN_STATES),
                    ShadowEvaluationRun.delete_at > now,
                )
            )
            if open_run is not None:
                continue
            if settings.photo_reading_enabled and settings.photo_backfill_since is not None:
                await reopen_unread_photo_messages(db, group, settings.photo_backfill_since, now)
            planned = await _plan_next(db, group, route, settings, now, provider, transport)
            if planned is None:
                continue
            if planned == "photo_retry":
                return "photo_retry"
            package, memory, revision, run_key, generation, photo_cost = planned
            existing = await db.scalar(
                select(ShadowEvaluationRun).where(
                    ShadowEvaluationRun.group_id == group.id,
                    ShadowEvaluationRun.raw_message_id.is_(None),
                    ShadowEvaluationRun.source_snapshot_sha256 == run_key,
                )
            )
            if existing is not None:
                # The very same package already ended without completing its targets: do not
                # loop on it. A failed run parks its targets as failed (visible, requeueable).
                await db.execute(
                    update(RawMessage)
                    .where(
                        RawMessage.group_id == group.id,
                        RawMessage.telegram_message_id.in_(package.target_ids),
                        RawMessage.revision == revision,
                        RawMessage.processing_state == "pending",
                    )
                    .values(
                        processing_state="failed" if existing.state == "failed" else "completed"
                    )
                )
                await db.commit()
                return "settled"
            db.add(
                ShadowEvaluationRun(
                    group_id=group.id,
                    batch_id=None,
                    job_id=None,
                    raw_message_id=None,
                    source_revision=revision,
                    analysis_generation=None,
                    source_snapshot_sha256=run_key,
                    engine_version=route.engine_version,
                    state="queued",
                    input_json=json.dumps(
                        package.analysis_input.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    memory_json=json.dumps(memory, ensure_ascii=False, sort_keys=True),
                    metrics_json=json.dumps(
                        {
                            "package": {
                                "key": run_key,
                                "targets": list(package.target_ids),
                                "revision": revision,
                                "generation": generation,
                            }
                        },
                        sort_keys=True,
                    ),
                    attempts=0,
                    available_at=now,
                    prompt_tokens=0,
                    completion_tokens=0,
                    prompt_cache_hit_tokens=0,
                    reserved_usd=Decimal(0),
                    charged_usd=photo_cost,
                    created_at=now,
                    delete_at=now + RUN_RETENTION,
                )
            )
            await db.commit()
            return "queued"
    return "idle"


async def _existing_items(db, group_id, now):
    """Trusted identities of the group's recent active cards (id + revision the model must copy).

    Without them the model cannot update or cancel an earlier card, and a later mention of the
    same task would become a second card. Read at run time so revisions are fresh.
    """
    rows = (
        await db.scalars(
            select(CanonicalAcademicItem)
            .where(
                CanonicalAcademicItem.group_id == group_id,
                CanonicalAcademicItem.state == "active",
                CanonicalAcademicItem.updated_at >= now - EXISTING_ITEMS_WINDOW,
            )
            .order_by(CanonicalAcademicItem.updated_at.desc(), CanonicalAcademicItem.id)
            .limit(EXISTING_ITEMS_LIMIT)
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "revision": row.revision,
            "semantic_kind": row.semantic_kind,
            "subject": row.subject,
            "title": row.title[:200],
            "description": (row.description or "")[:600],
            "deadline_at": _utc(row.deadline_at).isoformat() if row.deadline_at else None,
            "deadline_basis": row.deadline_basis,
        }
        for row in rows
    ]


def _full_names(subjects):
    """Map every timetable name to its full form.

    Old timetables carry names cut off with an ellipsis ("Русский язык как…"). A cut-off name is
    expanded to the one full name it is a prefix of; with no unique full name it is kept as it is.
    """
    full = {name for name in subjects if not name.endswith("…")}
    mapping = {}
    for name in subjects:
        if name in full:
            mapping[name] = name
            continue
        prefix = " ".join(name.rstrip("…").casefold().replace("ё", "е").split())
        hits = {
            candidate
            for candidate in full
            if " ".join(candidate.casefold().replace("ё", "е").split()).startswith(prefix)
        }
        mapping[name] = next(iter(hits)) if len(hits) == 1 else name
    return mapping


async def _subject_context(db, group):
    """The group's timetable in the shape the subject resolver needs (None without patterns)."""
    patterns = (
        await db.scalars(select(SchedulePattern).where(SchedulePattern.group_id == group.id))
    ).all()
    if not patterns:
        return None
    names = _full_names({row.subject for row in patterns})
    lessons = [
        Lesson(
            row.weekday,
            row.starts,
            row.ends,
            names[row.subject],
            None if row.week in (None, "all") else row.week,
        )
        for row in patterns
    ]
    return {
        "lessons": lessons,
        "names": sorted(set(names.values())),
        "timezone": group.timezone,
        "covered_from": min(row.valid_from for row in patterns),
        "covered_to": max(row.valid_until for row in patterns),
        "first_week_anchor": group.first_week_anchor,
    }


async def _watch_duplicates(db, row, group, now):
    """Second line against duplicate cards: report, never merge (see duplicate_guard)."""
    touched = (
        await db.scalars(
            select(CanonicalItemEvidence.item_id)
            .join(RawMessage, RawMessage.id == CanonicalItemEvidence.raw_message_id)
            .where(
                RawMessage.group_id == group.id,
                RawMessage.telegram_message_id.in_(_targets(row)),
            )
            .distinct()
        )
    ).all()
    pairs = await duplicate_guard.watch(db, group.id, touched) if touched else []
    if pairs:
        metrics = _metrics(row)
        metrics["duplicate_watch"] = [list(pair) for pair in pairs[:20]]
        row.metrics_json = json.dumps(metrics, sort_keys=True)
        await incident(db, "possible_duplicate_cards", now, detail=f"pairs={len(pairs)}")
    else:
        await incident(db, "possible_duplicate_cards", now, recover=True)


HELD_STATUSES = ("review", "blocked_by_lock", "stale")


# What a student can see on a card. Everything else in a revision (attention reasons, why the
# deadline was chosen, locks, timezone confirmation) is technical: a revision that changed only
# those is not an update the owner needs to hear about.
VISIBLE_FIELDS = {
    "title": "заголовок",
    "description": "описание",
    "subject": "предмет",
    "semantic_kind": "тип",
    "deadline_at": "срок",
    "deadline_date_only": "срок",
    "deadline_basis": "срок",
    "audience_scope": "аудитория",
    "audience_label": "аудитория",
    "state": "статус",
}
OWNER_LINES = 5


async def _revision_snapshot(db, item_id, revision):
    row = await db.scalar(
        select(CanonicalItemRevision).where(
            CanonicalItemRevision.item_id == item_id,
            CanonicalItemRevision.item_revision == revision,
        )
    )
    return json.loads(row.snapshot_json) if row else None


async def _visible_changes(db, row):
    """[(action, title, [changed visible fields])] of the cards this package wrote.

    An update whose revision differs from the previous one only in technical fields is dropped.
    """
    found = []
    for item_id, revision, action in (_metrics(row).get("promotion") or {}).get("changes", []):
        item_id = uuid.UUID(item_id)
        current = await _revision_snapshot(db, item_id, revision)
        if current is None:
            continue
        title = current.get("title") or "Без названия"
        if action == "created":
            found.append(("created", title, []))
            continue
        previous = await _revision_snapshot(db, item_id, revision - 1)
        if previous is None:
            found.append(("updated", title, []))  # nothing to compare with: report, do not guess
            continue
        fields = list(
            dict.fromkeys(
                label
                for key, label in VISIBLE_FIELDS.items()
                if previous.get(key) != current.get(key)
            )
        )
        if fields:
            found.append(("updated", title, fields))
    return found


async def _tell_the_owner(db, settings, row, group):
    """One short private message after a package that CHANGED something; silence otherwise.

    Counts, cost and the titles of the cards it wrote (the owner's private channel only). An
    "updated" card is counted only when a student-visible field changed. Skipped unless the
    owner feed is configured. The outbox key is the run id, so a retried commit can never send
    it twice.
    """
    if not settings.owner_update_notifications_enabled or not settings.owner_telegram_user_id:
        return
    statuses = (_metrics(row).get("promotion") or {}).get("statuses", {})
    changes = await _visible_changes(db, row)
    created = sum(1 for action, _, _ in changes if action == "created")
    updated = sum(1 for action, _, _ in changes if action == "updated")
    held = sum(statuses.get(name, 0) for name in HELD_STATUSES)
    if created + updated + held == 0:
        return
    parts = []
    if created:
        parts.append(f"создано карточек: {created}")
    if updated:
        parts.append(f"обновлено: {updated}")
    if held:
        parts.append(f"ждут проверки: {held}")
    text = (
        f"Новый движок: пакет из {len(_targets(row))} сообщений разобран, "
        + ", ".join(parts)
        + f". Расход ${Decimal(row.charged_usd):.4f}."
    )
    lines = [
        f"• «{title[:80]}» — " + (", ".join(fields) if fields else "новая")
        for _, title, fields in changes[:OWNER_LINES]
    ]
    if len(changes) > OWNER_LINES:
        lines.append(f"и ещё {len(changes) - OWNER_LINES}")
    if lines:
        text += "\n" + "\n".join(lines)
    await queue_owner_message(db, settings, f"package:{row.id}", group, text)


def _package_line(row, outcome, *, result=None, failure=None):
    """One line per package for the operator: counts and codes only, never message text.

    Built BEFORE the commit (the row's attributes expire on commit) and printed after it.
    """
    statuses = (_metrics(row).get("promotion") or {}).get("statuses", {})
    return "PACKAGE_RESULT " + json.dumps(
        {
            "outcome": outcome,
            "targets": len(_targets(row)),
            "attempt": row.attempts,
            "accepted": 0 if result is None else len(result.validation.accepted),
            "rejected": 0 if result is None else len(result.validation.rejected),
            "created": statuses.get("created", 0),
            "updated": statuses.get("updated", 0),
            "held": statuses.get("review", 0),
            "error": None if failure is None else failure.code,
            "charged_usd": str(row.charged_usd),
        },
        sort_keys=True,
    )


def _targets(row):
    package = _metrics(row).get("package") or {}
    targets = package.get("targets")
    return [value for value in targets if isinstance(value, int)] if targets else []


async def _finish_targets(db, row, state):
    await db.execute(
        update(RawMessage)
        .where(
            RawMessage.group_id == row.group_id,
            RawMessage.telegram_message_id.in_(_targets(row)),
            RawMessage.revision == row.source_revision,
            RawMessage.processing_state == "pending",
        )
        .values(processing_state=state)
    )


async def process_package_next(engine, settings, provider=None, now=None):
    """Plan, run and apply one package of a ``new`` group; returns a short status string."""
    now = _utc(now or datetime.now(UTC))
    if not _gate(settings, now):
        return "disabled"
    queued = await enqueue_next_package(engine, settings, now, provider)
    claimed = await _claim(
        engine,
        now,
        mode="new",
        hourly_cap=Decimal(str(settings.new_engine_hourly_budget_usd)),
    )
    if claimed is None:
        return "idle" if queued in {"idle", "settled"} else queued
    if isinstance(claimed, str):
        return claimed
    run_id, generation = claimed
    async with AsyncSession(engine) as db:
        row = await db.get(ShadowEvaluationRun, run_id)
        input_json, memory_json = row.input_json, row.memory_json
        existing_items = await _existing_items(db, row.group_id, now)
    failure = None
    result = None
    try:
        analysis_input = AnalysisInput.model_validate_json(input_json)
        memory = json.loads(memory_json or "[]")
        if provider is None:
            from studgroup.ai import DeepSeekProvider

            provider = DeepSeekProvider(
                settings.deepseek_api_key.get_secret_value(),
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
                allow_technical_repair=settings.ai_technical_repair_enabled,
            )
        result = await run_shadow(
            provider,
            analysis_input,
            memory,
            repair_enabled=getattr(settings, "ai_output_repair_enabled", False),
            existing_items=existing_items,
            # an out-of-window reference in the advisory context must not lose a 24-message package
            lenient_context=True,
        )
    except ShadowPassFailure as error:
        failure = error
    except ProviderFailure as error:
        failure = ShadowPassFailure(
            error.code,
            error.retryable,
            usage=error.usage,
            usage_complete=error.usage is not None or error.reservation_releasable,
            reservation_releasable=error.reservation_releasable,
        )
    except Exception:  # noqa: BLE001 - durable boundary must recover unknown worker faults
        # No exception text is persisted because provider errors may contain private data.
        failure = ShadowPassFailure("package_pipeline_error", True)
    async with AsyncSession(engine) as db:
        row = await db.scalar(
            select(ShadowEvaluationRun).where(ShadowEvaluationRun.id == run_id).with_for_update()
        )
        if row.state != "running" or row.attempts != generation:
            return "superseded"
        usage = result if result is not None else failure.usage
        actual = usage_cost_usd(usage) + (result.extra_charge_usd if result is not None else 0)
        row.prompt_tokens += usage.prompt_tokens
        row.completion_tokens += usage.completion_tokens
        row.prompt_cache_hit_tokens += usage.prompt_cache_hit_tokens
        row.reserved_usd = Decimal(0)
        row.lease_until = None
        group = await db.get(Group, row.group_id)
        if result is not None:
            row.charged_usd += actual
            row.context_result_json = result.context.model_dump_json()
            row.validation_result_json = result.validation.model_dump_json()
            row.provider_payload_hashes = json.dumps(result.provider_payload_hashes)
            carried = {k: v for k, v in _metrics(row).items() if k in ("package", "wave")}
            row.metrics_json = json.dumps(
                carried
                | {
                    "accepted_items": len(result.validation.accepted),
                    "rejected_items": len(result.validation.rejected),
                    "raw_proposal_count": result.validation.raw_proposal_count,
                    "adapter_actions": result.validation.adapter_actions,
                    "repair": result.repair,
                    "subject_catalog": catalog_summary(
                        AnalysisInput.model_validate_json(row.input_json).subject_catalog
                    ),
                },
                sort_keys=True,
            )
            row.latency_ms = result.latency_ms
            row.state = "completed"
            row.completed_at = now
            if result.validation.accepted:
                subjects = (
                    await db.scalars(
                        select(SchedulePattern.subject)
                        .where(SchedulePattern.group_id == group.id)
                        .distinct()
                    )
                ).all()
                promoted = await promote_run(
                    db,
                    group=group,
                    run=row,
                    validation=result.validation,
                    now=now,
                    include_expired=True,
                    apply=True,
                    actor="package-worker",
                    emit_notifications=settings.new_engine_notifications,
                    verified_subjects=tuple(subjects),
                    deadline_context=await build_deadline_context(db, group),
                    subject_context=await _subject_context(db, group),
                )
                if promoted != "runs_promoted":
                    # Evidence changed after the run: keep the validated proposals on the run for
                    # the owner to inspect/promote; nothing is silently dropped.
                    metrics = _metrics(row)
                    metrics["promotion_skipped"] = promoted
                    row.metrics_json = json.dumps(metrics, sort_keys=True)
            await _finish_targets(db, row, "completed")
            await _watch_duplicates(db, row, group, now)
            await _tell_the_owner(db, settings, row, group)
            await incident(db, "ai_daily_group_budget_limit", now, recover=True)
            line = _package_line(row, "completed", result=result)
            await db.commit()
            print(line, flush=True)
            return "completed"
        # If the provider did not report usage, keep the full reservation as a
        # conservative charge unless it explicitly proved that no request ran.
        row.charged_usd += actual if failure.usage_complete else max(actual, SHADOW_RESERVATION_USD)
        row.provider_payload_hashes = json.dumps(failure.hashes)
        if failure.raw_results:
            row.context_result_json = failure.raw_results[0]
        if len(failure.raw_results) > 1:
            row.validation_result_json = failure.raw_results[1]
        row.last_error = failure.code
        # A malformed or schema-invalid model answer is a bad sample, not a defect of the package:
        # the next attempt is usually valid, and giving up would park the whole package (up to 24
        # messages) as failed. Both kinds are retried within the attempt limit.
        retryable = failure.retryable or failure_stage(failure.code) == "validation"
        if retryable and row.attempts - attempt_base(row) < SHADOW_MAX_ATTEMPTS:
            row.state = "retry"
            row.available_at = now + timedelta(seconds=15 * 2 ** (row.attempts - 1))
            outcome = "retry"
        else:
            row.state = "failed"
            row.completed_at = now
            await _finish_targets(db, row, "failed")
            await incident_once(db, "processing_exhausted", now, detail=failure.code)
            outcome = "failed"
        line = _package_line(row, outcome, failure=failure)
        await db.commit()
        print(line, flush=True)
        return outcome
