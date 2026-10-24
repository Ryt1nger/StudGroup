"""Subjects taught in subgroups: setup, members' choices, recognition and who sees which card.

Product rules (owner, 2026-10-24):
* the headman lists the subjects that split, how many subgroups each has and the signals that
  recognise a subgroup in a message (teacher's surname, group number, anything); the AI turns the
  signals into a short name students pick from, and the headman can rename;
* a student picks a subgroup per split subject after joining (the bot also asks members who joined
  earlier) and can change it later; until then the student sees every subgroup's cards;
* a card of a split subject whose subgroup is not recognised is shown to everyone of the group and
  goes to the headman's review queue;
* headman and deputy always see every card.

Visibility is decided here, on the server; the client only renders what it gets.
"""

import json
import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, true, update

from studgroup.models import (
    AcademicDeadline,
    Homework,
    Membership,
    MemberSubgroup,
    Subgroup,
    SubjectSplit,
)
from studgroup.subject_identity import match_key, subject_uuid

log = logging.getLogger(__name__)

MAX_SUBGROUPS = 6
MAX_SIGNALS = 12
MAX_NAME = 40
EDITOR_ROLES = {"headman", "deputy"}
# Signals this short ("1", "А", "ИВТ") are too common to look for in free text; they still count
# when the AI names the subgroup with them.
MIN_TEXT_SIGNAL = 4


def parse_signals(text):
    """Signals from what the headman typed: separated by commas, semicolons or new lines."""
    parts = re.split(r"[,;\n]+", text or "")
    seen, result = set(), []
    for part in parts:
        value = " ".join(part.split())[:80]
        if value and match_key(value) not in seen:
            seen.add(match_key(value))
            result.append(value)
    return result[:MAX_SIGNALS]


def signals_of(subgroup):
    try:
        value = json.loads(subgroup.signals or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, str) and item.strip()]


def fallback_name(position, signals):
    """A readable name without the AI: «Подгруппа 1 · Иванова»."""
    base = f"Подгруппа {position}"
    words = [s for s in signals if re.search(r"[A-Za-zА-Яа-яЁё]{3,}", s)]
    return f"{base} · {words[0]}"[:MAX_NAME] if words else base


NAMING_PROMPT = """You name subgroups of one university subject for a student app. Input: the
subject and, for each subgroup in order, the signals the group's headman uses to recognise it
(teacher's surname, group number, room, anything). Produce one short Russian name per subgroup,
at most 40 characters, that a student instantly recognises: keep the number if one is given and
the teacher's surname as written, for example «Гр. 1 · Иванова» or «Группа Петрова». Never invent
a number, a surname or a detail that is not in the signals, never repeat the subject, and make the
names differ from each other. Answer JSON only: {"names": ["...", "..."]} with exactly one name per
subgroup, in the same order."""


async def ai_names(settings, subject, signals, provider=None):
    """Names from the AI, or None when it is not configured, fails or answers out of shape."""
    from studgroup.ai import DeepSeekProvider, ProviderFailure
    from studgroup.analysis.provider import PreparedRequest

    try:
        if provider is None:
            key = settings.deepseek_api_key.get_secret_value() if settings else ""
            if not key.strip():
                return None
            provider = DeepSeekProvider(
                key, model=settings.deepseek_model, base_url=settings.deepseek_base_url
            )
        response = await provider.complete(
            PreparedRequest(
                system_prompt=NAMING_PROMPT,
                user_payload={
                    "subject": subject,
                    "subgroups": [{"signals": items} for items in signals],
                },
                schema_version="subgroup-names-1",
                prompt_version="subgroup-names-1",
                max_output_tokens=300,
            )
        )
        names = json.loads(response.content)["names"]
    except (ProviderFailure, KeyError, TypeError, ValueError) as error:
        log.info("subgroup naming fell back: %s", type(error).__name__)
        return None
    if not isinstance(names, list) or len(names) != len(signals):
        return None
    cleaned = [" ".join(str(name).split())[:MAX_NAME] for name in names]
    if any(not name for name in cleaned) or len({match_key(n) for n in cleaned}) != len(cleaned):
        return None
    return cleaned


async def suggest_names(settings, subject, signals, provider=None):
    names = await ai_names(settings, subject, signals, provider)
    return names or [fallback_name(n + 1, items) for n, items in enumerate(signals)]


async def splits(db, group_id):
    """``[(split, [subgroups by position])]`` of a group, by subject name."""
    rows = (await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == group_id))).all()
    if not rows:
        return []
    members = (
        await db.scalars(
            select(Subgroup)
            .where(Subgroup.split_id.in_([row.id for row in rows]))
            .order_by(Subgroup.position)
        )
    ).all()
    by_split = {}
    for subgroup in members:
        by_split.setdefault(subgroup.split_id, []).append(subgroup)
    return sorted(
        ((row, by_split.get(row.id, [])) for row in rows),
        key=lambda pair: pair[0].subject_name.casefold(),
    )


def split_for(all_splits, group_id, subject):
    """The split of a subject (by id, else by lenient wording), or None."""
    if not subject:
        return None
    sid = subject_uuid(group_id, subject)
    for split, members in all_splits:
        if split.subject_id == sid:
            return split, members
    key = match_key(subject)
    found = [pair for pair in all_splits if match_key(pair[0].subject_name) == key]
    return found[0] if len(found) == 1 else None


# Words of a signal that say nothing about WHICH subgroup it is.
GENERIC_WORDS = {
    "группа",
    "гр",
    "подгруппа",
    "подгр",
    "пг",
    "ауд",
    "аудитория",
    "каб",
    "кабинет",
    "преподаватель",
    "преп",
    "препод",
    "у",
}


def _stem(word):
    """Russian surnames inflect («Иванова» → «Ивановой»): drop one ending vowel before matching."""
    return word[:-1] if len(word) >= 5 and word[-1] in "аяоеыийь" else word


def _word_in(word, haystack):
    pattern = re.escape(_stem(word)) + r"\w{0,2}"
    return re.search(rf"(?<!\w){pattern}(?!\w)", haystack) is not None


def _mentions(signal, text, *, short_ok):
    """True when ``text`` names ``signal``: the whole signal as written, or (in free text) one of
    its distinctive words such as a surname, in any grammatical case."""
    key, haystack = match_key(signal), f" {match_key(text)} "
    if not key:
        return False
    if (short_ok or len(key.replace(" ", "")) >= MIN_TEXT_SIGNAL) and f" {key} " in haystack:
        return True
    words = [
        word
        for word in key.split()
        if word not in GENERIC_WORDS
        and re.fullmatch(r"[^\W\d_]+", word)
        and len(word) >= MIN_TEXT_SIGNAL
    ]
    return any(_word_in(word, haystack) for word in words)


ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6}
_NUMBERED = re.compile(
    r"(?<!\w)(?:(\d{1,2})\s*(?:я|ая|-?я)?\s*(?:под)?гр\w*|(?:под)?гр\w*\s*(?:№\s*)?(\d{1,2}))(?!\w)"
    r"|(?<!\w)(перв|втор|трет|четверт|пят|шест)\w*\s+(?:под)?гр\w*",
    re.IGNORECASE,
)


def numbers(text, *, bare=False):
    """Subgroup numbers a text names («2 группа», «гр. 2», «вторая подгруппа»; with ``bare``
    any standalone number, as in a label the AI wrote)."""
    found = set()
    value = (text or "").casefold().replace("ё", "е").replace(".", " ")
    for match in _NUMBERED.finditer(value):
        digits = match.group(1) or match.group(2)
        found.add(int(digits) if digits else ORDINALS[match.group(3)])
    if bare:
        found |= {int(n) for n in re.findall(r"(?<!\d)(\d{1,2})(?!\d)", value)}
    return found


def own_numbers(subgroup):
    """Numbers that identify a subgroup: those in its signals and name, else its position."""
    found = set()
    for item in [*signals_of(subgroup), subgroup.name]:
        found |= numbers(item, bare=True)
    return found or {subgroup.position}


def _single(members, test):
    hit = [m for m in members if test(m)]
    return hit[0] if len(hit) == 1 else None


def recognise(members, label=None, text=""):
    """The subgroup a card belongs to, or None when nothing (or more than one) matches.

    The AI's label decides first (it was given these names and signals); the cited message text
    is the fallback, and only for distinctive signals (a surname) or an explicit «2 подгруппа».
    """
    if label:
        found = (
            _single(members, lambda m: match_key(m.name) == match_key(label))
            or _single(
                members, lambda m: any(_mentions(s, label, short_ok=True) for s in signals_of(m))
            )
            or _single(members, lambda m: bool(own_numbers(m) & numbers(label, bare=True)))
        )
        if found:
            return found
    if text:
        return _single(
            members, lambda m: any(_mentions(s, text, short_ok=False) for s in signals_of(m))
        ) or _single(members, lambda m: bool(own_numbers(m) & numbers(text)))
    return None


async def assign(db, group_id, subject, label, text, all_splits=None):
    """``(subgroup_id_or_None, unresolved)`` for a card: unresolved means a split subject whose
    subgroup could not be told, which the headman must look at."""
    if all_splits is None:
        with db.no_autoflush:  # the caller's card may still be half-filled
            all_splits = await splits(db, group_id)
    found = split_for(all_splits, group_id, subject)
    if found is None:
        return None, False
    subgroup = recognise(found[1], label, text)
    return (subgroup.id, False) if subgroup else (None, True)


async def membership(db, group_id, user_id):
    return await db.scalar(
        select(Membership).where(
            Membership.group_id == group_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )


async def choices(db, membership_id):
    """``{split_id: subgroup_id}`` the member picked."""
    rows = (
        await db.scalars(
            select(MemberSubgroup).where(MemberSubgroup.membership_id == membership_id)
        )
    ).all()
    return {row.split_id: row.subgroup_id for row in rows}


async def hidden(db, group_id, user_id):
    """Subgroup ids whose cards this user must not see: the other subgroups of every split
    subject the user answered. Editors and people without a choice see everything."""
    member = await membership(db, group_id, user_id) if user_id else None
    if member is None or member.role in EDITOR_ROLES:
        return set()
    picked = await choices(db, member.id)
    if not picked:
        return set()
    others = await db.scalars(
        select(Subgroup.id).where(
            Subgroup.split_id.in_(list(picked)), Subgroup.id.not_in(list(picked.values()))
        )
    )
    return set(others.all())


def visible(column, hidden_ids):
    """SQL condition for a card's ``subgroup_id`` column."""
    if not hidden_ids:
        return true()
    return column.is_(None) | column.not_in(list(hidden_ids))


async def label(db, subgroup_id):
    """``{"id", "name"}`` of a card's subgroup for the API, or None."""
    if subgroup_id is None:
        return None
    row = await db.get(Subgroup, subgroup_id)
    return {"id": row.id, "name": row.name} if row else None


async def choose(db, member, split_id, subgroup_id, now=None):
    """Record (or with ``subgroup_id=None`` clear) a member's subgroup for one split."""
    split = await db.get(SubjectSplit, split_id)
    if split is None or split.group_id != member.group_id:
        raise ValueError("unknown_split")
    if subgroup_id is None:
        await db.execute(
            delete(MemberSubgroup).where(
                MemberSubgroup.membership_id == member.id, MemberSubgroup.split_id == split_id
            )
        )
        return None
    subgroup = await db.get(Subgroup, subgroup_id)
    if subgroup is None or subgroup.split_id != split_id:
        raise ValueError("unknown_subgroup")
    row = await db.get(MemberSubgroup, (member.id, split_id))
    if row is None:
        row = MemberSubgroup(membership_id=member.id, split_id=split_id, subgroup_id=subgroup.id)
        db.add(row)
    row.subgroup_id = subgroup.id
    row.updated_at = now or datetime.now(UTC)
    await db.flush()
    return subgroup


async def unanswered(db, member):
    """Splits of the member's group the member has not picked a subgroup for yet."""
    picked = await choices(db, member.id)
    return [pair for pair in await splits(db, member.group_id) if pair[0].id not in picked]


async def create_split(db, group, subject, entries, now=None):
    """A new split subject with its subgroups ``[(name, [signals])]``; replaces an earlier one."""
    if not 2 <= len(entries) <= MAX_SUBGROUPS:
        raise ValueError("subgroup_count")
    now = now or datetime.now(UTC)
    sid = subject_uuid(group.id, subject)
    earlier = await db.scalar(
        select(SubjectSplit).where(
            SubjectSplit.group_id == group.id, SubjectSplit.subject_id == sid
        )
    )
    if earlier is not None:
        await delete_split(db, group.id, earlier.id)
    split = SubjectSplit(
        id=uuid.uuid4(), group_id=group.id, subject_id=sid, subject_name=subject, created_at=now
    )
    db.add(split)
    await db.flush()
    for position, (name, items) in enumerate(entries, start=1):
        db.add(
            Subgroup(
                split_id=split.id,
                position=position,
                name=(name or fallback_name(position, items))[:MAX_NAME],
                signals=json.dumps(items[:MAX_SIGNALS], ensure_ascii=False),
            )
        )
    await db.flush()
    return split


async def delete_split(db, group_id, split_id):
    """Remove a split: its cards become cards of the whole group again (FK sets them to NULL)."""
    split = await db.get(SubjectSplit, split_id)
    if split is None or split.group_id != group_id:
        raise ValueError("unknown_split")
    ids = select(Subgroup.id).where(Subgroup.split_id == split_id)
    for model in (Homework, AcademicDeadline):
        await db.execute(update(model).where(model.subgroup_id.in_(ids)).values(subgroup_id=None))
    await db.execute(delete(MemberSubgroup).where(MemberSubgroup.split_id == split_id))
    await db.execute(delete(Subgroup).where(Subgroup.split_id == split_id))
    await db.delete(split)
    await db.flush()


async def notify_students(db, settings, group, split):
    """Tell existing students that one new choice is waiting for them."""
    from studgroup.bot_admin import enqueue
    from studgroup.models import User

    telegram_ids = (
        await db.scalars(
            select(User.telegram_user_id)
            .join(Membership, Membership.user_id == User.id)
            .where(
                Membership.group_id == group.id,
                Membership.role == "student",
                Membership.status == "active",
            )
        )
    ).all()
    username = (settings.bot_username or "").lstrip("@")
    for telegram_id in telegram_ids:
        payload = {
            "chat_id": telegram_id,
            "text": (
                f"В группе «{group.name}» появились подгруппы по предмету "
                f"«{split.subject_name}». Выберите свою — это определит, какие карточки "
                "по предмету вы будете видеть. Пока вы не ответили, видны все карточки."
            ),
            "link_preview_options": {"is_disabled": True},
        }
        if username:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {
                            "text": "Выбрать подгруппу",
                            "url": f"https://t.me/{username}?start=subgroups",
                        }
                    ]
                ]
            }
        enqueue(db, "sendMessage", payload)
