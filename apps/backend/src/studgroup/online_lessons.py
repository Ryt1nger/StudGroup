"""Joining URLs from retained source evidence. Never fetch URLs or log room passwords."""

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import case, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from studgroup.models import (
    Group,
    HeadmanDecision,
    LessonOnlineLink,
    RawMessage,
    ScheduleException,
    SchedulePattern,
)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def joining_url(url):
    if len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
        ):
            return False
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.path in ("", "/") or parsed.path.casefold().endswith(
        (".pdf", ".pptx", ".docx", ".xlsx", ".zip", ".exe")
    ):
        return False
    for domain, paths in {
        "mts-link.ru": ("/j/",),
        "webinar.ru": ("/",),
        "zoom.us": ("/j/", "/my/"),
        "meet.google.com": ("/",),
        "teams.microsoft.com": ("/l/meetup-join/",),
        "telemost.yandex.ru": ("/j/",),
        "ranepa.ru": ("/mod/bigbluebuttonbn/", "/mod/webinar/"),
    }.items():
        if (host == domain or host.endswith("." + domain)) and parsed.path.startswith(paths):
            return True
    return False


def urls(text):
    found = []
    identities = set()
    for match in re.findall(r"https://[^\s<>\"']+", text, re.IGNORECASE):
        url = match.rstrip(".,;!)]}")
        if joining_url(url):
            parsed = urlsplit(url)
            identity = (parsed.hostname, parsed.path)
            if identity not in identities:
                identities.add(identity)
                found.append(url)
    return found[:4]


def normalize(value):
    return " ".join(value.casefold().replace("ё", "е").split())


def subject_from(text, patterns):
    names = {p.subject for p in patterns}
    clean = normalize(re.sub(r"https?://\S+", "", text, flags=re.IGNORECASE))
    matches = {name for name in names if normalize(name) in clean}
    aliases = {
        "матан": "математический анализ",
        "история": "история россии",
        "культура": "культура личности",
        "орг": "основы российской государственности",
        "линал": "линейная алгебра",
        "русский": "русский язык",
        "английский": "иностранный язык",
        "инфа": "основы информатики",
    }
    for alias, stem in aliases.items():
        if re.search(r"(?<!\w)#?" + re.escape(alias) + r"(?!\w)", clean):
            matches.update(name for name in names if normalize(name).startswith(stem))
    return next(iter(matches)) if len(matches) == 1 else None


async def discover(
    db,
    raw,
    *,
    subject=None,
    lesson_date=None,
    permanent=False,
    confidence=100,
    only_url=None,
    lesson_time=None,
):
    from studgroup.api import schedule_data
    from studgroup.deadlines import contextual_deadline
    from studgroup.notifications import record_change

    now = datetime.now(UTC)
    if confidence < 85:
        return 0
    group = await db.get(Group, raw.group_id)
    root, context = raw, None
    direct = urls(raw.text or "")
    if not direct and raw.reply_to_message_id:
        parent = await db.scalar(
            select(RawMessage).where(
                RawMessage.group_id == raw.group_id,
                RawMessage.telegram_message_id == raw.reply_to_message_id,
                RawMessage.delete_at > now,
            )
        )
        if parent and urls(parent.text or ""):
            root, context = parent, raw
            direct = urls(parent.text)
    if only_url:
        if only_url not in direct:
            return 0
        direct = [only_url]
    old = (
        await db.scalars(
            select(LessonOnlineLink).where(
                LessonOnlineLink.group_id == raw.group_id,
                or_(LessonOnlineLink.source_id == root.id, LessonOnlineLink.context_id == raw.id),
                LessonOnlineLink.url == only_url if only_url else True,
            )
        )
    ).all()
    previously_active = {link.id for link in old if link.state == "active"}
    for link in old:
        link.state = "superseded"
    if not direct or utc(root.delete_at) <= now:
        return 0
    patterns = (
        await db.scalars(
            select(SchedulePattern).where(
                SchedulePattern.group_id == group.id, SchedulePattern.cancelled.is_(False)
            )
        )
    ).all()
    evidence = root.text + ("\n" + context.text if context else "")
    explicit_subject = subject_from(evidence, patterns)
    subject = explicit_subject or (subject_from(subject, patterns) if subject else None)
    # Global repetition requires actual wording, not a model's guess.
    fixed = bool(
        re.search(r"постоянн\w*\s+ссылк|ссылк\w*\s+(?:на\s+сдо\s+)?постоянн", normalize(root.text))
    )
    language = re.sub(r"https?://\S+", "", normalize(root.text))
    uncertain = bool(re.search(r"вроде|наверно|кажется|возможно|\?", language))
    negative_permanence = bool(re.search(r"\bне\s*постоян|одноразов", language))
    permanent = fixed and not uncertain and not negative_permanence
    stamp = utc(root.message_date).astimezone(ZoneInfo(group.timezone))
    day = lesson_date or stamp.date()
    language_evidence = re.sub(r"https?://\S+", "", evidence, flags=re.IGNORECASE)
    resolution = contextual_deadline(
        language_evidence,
        utc(context.message_date if context else root.message_date),
        group.timezone,
    )
    if resolution and resolution.at:
        day = resolution.at.astimezone(ZoneInfo(group.timezone)).date()
    if isinstance(day, str):
        day = date.fromisoformat(day)
    if abs((day - stamp.date()).days) > 31:
        return 0
    targets = []
    if len(direct) > 1 and not only_url:
        targets = []
    elif permanent and subject:
        targets = [
            (p.id, None)
            for p in patterns
            if p.subject == subject
            and p.valid_until >= stamp.date()
            and re.search(r"сдо|дистан|онлайн", normalize(p.location or ""))
        ]
    else:
        calendar = await schedule_data(day, day, group, db)
        candidates = [
            l
            for l in calendar["lessons"]
            if l["status"] == "scheduled"
            and (not subject or normalize(l["subject"]) == normalize(subject))
        ]
        if lesson_time:
            candidates = [
                l
                for l in candidates
                if datetime.fromisoformat(l["starts_at"])
                .astimezone(ZoneInfo(group.timezone))
                .strftime("%H:%M")
                == lesson_time
            ]
        if not subject:
            candidates = [
                l
                for l in candidates
                if re.search(r"сдо|дистан|онлайн", normalize(l["location"] or ""))
            ]
        if day == stamp.date():
            current = [
                l
                for l in candidates
                if datetime.fromisoformat(l["starts_at"])
                <= stamp
                < datetime.fromisoformat(l["ends_at"])
            ]
            candidates = current or [
                l
                for l in candidates
                if stamp
                <= datetime.fromisoformat(l["starts_at"])
                <= stamp + (timedelta(hours=4) if subject else timedelta(minutes=30))
            ]
        if len(candidates) == 1:
            targets = [(uuid.UUID(candidates[0]["id"].split(":")[0]), day)]
    bound = 0
    for url in direct:
        for pattern_id, occurrence in targets or [(None, None)]:
            key = f"online:{root.id}:{url}:{pattern_id}:{occurrence}"
            ident = uuid.uuid5(group.id, key)
            row = await db.get(LessonOnlineLink, ident)
            was_active = bool(
                row
                and row.id in previously_active
                and row.source_revision == root.revision
                and row.context_revision == (context.revision if context else None)
                and row.url == url
                and row.pattern_id == pattern_id
            )
            if row is None:
                row = LessonOnlineLink(id=ident, group_id=group.id, source_id=root.id, url=url)
                db.add(row)
            row.source_revision = root.revision
            row.context_id = context.id if context else None
            row.context_revision = context.revision if context else None
            row.pattern_id = pattern_id
            row.occurrence_date = occurrence
            row.state = "active" if pattern_id else "unmatched"
            row.updated_at = now
            row.delete_at = (
                min(utc(root.delete_at), utc(context.delete_at)) if context else utc(root.delete_at)
            )
            if pattern_id:
                bound += 1
                if not was_active:
                    pattern = next(p for p in patterns if p.id == pattern_id)
                    await record_change(
                        db,
                        root,
                        SimpleNamespace(
                            id=ident,
                            subject=pattern.subject,
                            title="Добавлена ссылка для подключения к дистанционному занятию",
                        ),
                        "schedule",
                        now,
                    )
    await db.flush()
    return bound


async def overlay(db, group, lessons):
    now = datetime.now(UTC)
    clarification = aliased(RawMessage)
    rows = (
        await db.scalars(
            select(LessonOnlineLink)
            .join(RawMessage, RawMessage.id == LessonOnlineLink.source_id)
            .outerjoin(clarification, clarification.id == LessonOnlineLink.context_id)
            .where(
                LessonOnlineLink.group_id == group.id,
                LessonOnlineLink.state == "active",
                LessonOnlineLink.delete_at > now,
            )
            .order_by(
                case(
                    (
                        clarification.version_date > RawMessage.version_date,
                        clarification.version_date,
                    ),
                    else_=RawMessage.version_date,
                ).desc(),
                LessonOnlineLink.updated_at.desc(),
            )
        )
    ).all()
    locked = set(
        (
            await db.scalars(
                select(HeadmanDecision.entity_key).where(
                    HeadmanDecision.group_id == group.id, HeadmanDecision.locked.is_(True)
                )
            )
        ).all()
    )
    for lesson in lessons:
        if lesson.get("online_url") or lesson["status"] == "cancelled":
            continue
        pattern, day = lesson["id"].split(":", 1)
        exception = await db.scalar(
            select(ScheduleException).where(
                ScheduleException.group_id == group.id,
                ScheduleException.pattern_id == uuid.UUID(pattern),
                ScheduleException.occurrence_date == date.fromisoformat(day),
            )
        )
        if f"p:{pattern}" in locked or (exception and f"x:{exception.id}" in locked):
            continue
        for row in rows:
            if str(row.pattern_id) != pattern or row.occurrence_date not in (
                None,
                date.fromisoformat(day),
            ):
                continue
            source = await db.get(RawMessage, row.source_id)
            context = await db.get(RawMessage, row.context_id) if row.context_id else None
            if (
                not source
                or source.group_id != group.id
                or source.revision != row.source_revision
                or utc(source.delete_at) <= now
            ):
                continue
            if row.context_revision is not None and (
                not context
                or context.group_id != group.id
                or context.revision != row.context_revision
                or utc(context.delete_at) <= now
            ):
                continue
            if joining_url(row.url):
                if row.occurrence_date is None and datetime.fromisoformat(lesson["ends_at"]) <= utc(
                    source.version_date
                ):
                    continue
                lesson["online_url"] = row.url
                break


async def backfill(engine):
    now = datetime.now(UTC)
    async with AsyncSession(engine) as db:
        sources = (
            await db.scalars(
                select(RawMessage)
                .join(Group)
                .where(
                    Group.pilot_authorized.is_(True),
                    Group.status == "active",
                    RawMessage.delete_at > now,
                    RawMessage.text.ilike("%https://%"),
                    or_(
                        *(
                            RawMessage.text.ilike("%" + host + "%")
                            for host in (
                                "mts-link.ru",
                                "zoom.us",
                                "webinar.ru",
                                "meet.google.com",
                                "teams.microsoft.com",
                                "telemost.yandex.ru",
                                "bigbluebuttonbn",
                            )
                        )
                    ),
                    ~exists(
                        select(LessonOnlineLink.id).where(
                            LessonOnlineLink.source_id == RawMessage.id,
                            LessonOnlineLink.source_revision == RawMessage.revision,
                        )
                    ),
                )
                .order_by(RawMessage.message_date)
                .limit(100)
            )
        ).all()
        bound = 0
        for raw in sources:
            if urls(raw.text):
                bound += await discover(db, raw)
        await db.commit()
    print(f"ONLINE_LESSON_LINK_BACKFILL sources={len(sources)} bound={bound}", flush=True)
