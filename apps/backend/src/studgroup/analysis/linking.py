"""Structural links between neighbouring messages, before and after the AI extraction.

Before the model runs, :func:`link_candidates` lists *potential* supplements for every message
of the frozen snapshot (earlier and later ones alike) with the evidence for each link. The
model gets them as hints. After extraction, :func:`reconcile_validation` checks that strong,
provable links were not lost and flags weak ones for review; it never merges two independent
assignments and never adds a fact that is not in a source message.

Strength:
* strong  - a direct reply to the assignment, or parts of one media group;
* medium  - reply to a reply, an enumeration or an explicit continuation by the same author in
            the same topic shortly after;
* weak    - a file from the same author in the same topic within minutes (adjacency only).
"""

import re
from collections import Counter
from dataclasses import dataclass

from studgroup.analysis.card_editor import supplement_adds_information
from studgroup.analysis.contracts import EvidenceRef, RejectedItem
from studgroup.analysis.text_hygiene import clean_text

STRONG, MEDIUM, WEAK = "strong", "medium", "weak"
RANK = {STRONG: 0, MEDIUM: 1, WEAK: 2}
MAX_PAYLOAD_LINKS = 40
STAFF_ROLES = {"headman", "deputy"}
ENUMERATION_START = re.compile(
    r"^\s*(?:\d{1,2}[).]|[-•*–—]|темы\b|варианты\b|список\b)", re.IGNORECASE
)
CONTINUATION_START = re.compile(
    r"^\s*(?:и\s+ещ[её]|а\s+также|ещ[её]\b|кстати|добавлю|добавляю|дополнени\w+|уточнени\w+|"
    r"уточняю|забыл\w*|и\s+\w+\s+тоже|нет[,\s])",
    re.IGNORECASE,
)
CONTENT_SIGNAL = re.compile(
    r"\d|стр\.?|номер|задач|упражнен|слайд|формат|pdf|docx|pptx|объ[её]м|минут|тем[аыу]\b|"
    r"вариант|требован|тоже|ещ[её]\b|также|нужно|надо|обязательно|сдать|прислать|отправить|"
    r"параграф|глав[аыу]|лист|страниц|http",
    re.IGNORECASE,
)
NEW_TASK = re.compile(
    r"\b(?:сделать|подготовить|написать|решить|выучить|принести|выполнить|прочитать|найти|"
    r"составить|сдать)\b",
    re.IGNORECASE,
)
PENDING_INTEGRATION = "supplement_pending_integration"
# Marks a cited source as a clarification reply that is kept as evidence for the card. It is the
# structured hand-over to the next AI pass: the item's sources say where the unintegrated text is.
SUPPLEMENT_PATH = "supplement_reply"


@dataclass(frozen=True)
class Link:
    anchor: int
    member: int
    relation: str
    strength: str
    minutes: float
    same_author: bool
    same_topic: bool


def _has_text(message):
    return bool((message.text or "").strip())


def _albums(message):
    return {a.media_group_id for a in message.attachments if a.media_group_id}


def _same_author(left, right):
    return bool(left.author_alias) and left.author_alias == right.author_alias


def _minutes(anchor, member):
    return (member.message_date - anchor.message_date).total_seconds() / 60


def _relation(anchor, member, by_id):
    """Strongest relation of ``member`` to ``anchor`` as (name, strength) or ``None``."""
    if member.reply_to_message_id == anchor.telegram_message_id:
        return "reply", STRONG
    if _albums(anchor) & _albums(member):
        return "album", STRONG
    if not _has_text(anchor):
        return None
    parent = by_id.get(member.reply_to_message_id)
    if parent is not None and parent.reply_to_message_id == anchor.telegram_message_id:
        return "reply_chain", MEDIUM
    gap = _minutes(anchor, member)
    same_topic = anchor.topic_id == member.topic_id
    same_author = _same_author(anchor, member)
    if same_topic and same_author and 0 < gap:
        if _has_text(member) and gap <= 60 and ENUMERATION_START.match(member.text):
            return "enumeration_after", MEDIUM
        if _has_text(member) and gap <= 360 and CONTINUATION_START.match(member.text):
            return "continuation_marker", MEDIUM
    if (
        same_topic
        and same_author
        and member.attachments
        and not _has_text(member)
        and abs(gap) <= 15
    ):
        return "adjacent_file", WEAK
    return None


def link_candidates(messages):
    by_id = {row.telegram_message_id: row for row in messages}
    links = []
    for anchor in messages:
        for member in messages:
            if anchor is member:
                continue
            found = _relation(anchor, member, by_id)
            if found is None:
                continue
            links.append(
                Link(
                    anchor.telegram_message_id,
                    member.telegram_message_id,
                    found[0],
                    found[1],
                    round(_minutes(anchor, member), 1),
                    _same_author(anchor, member),
                    anchor.topic_id == member.topic_id,
                )
            )
    return sorted(links, key=lambda row: (RANK[row.strength], abs(row.minutes), row.member))


def candidates_payload(messages):
    """Bounded, value-light hints for the model (ids and facts about the link only)."""
    by_id = {row.telegram_message_id: row for row in messages}
    payload = []
    for link in link_candidates(messages)[:MAX_PAYLOAD_LINKS]:
        member = by_id[link.member]
        payload.append(
            {
                "anchor_message_id": link.anchor,
                "member_message_id": link.member,
                "relation": link.relation,
                "strength": link.strength,
                "minutes_after_anchor": link.minutes,
                "same_author": link.same_author,
                "same_topic": link.same_topic,
                "member_author_role": member.author_role,
                "member_has_text": _has_text(member),
                "member_file_kinds": sorted({a.kind for a in member.attachments}),
            }
        )
    return payload


def _staff(message):
    return message.author_role in STAFF_ROLES


def _connected(left, right, by_id):
    """Is there any structural reason for these two cited messages to belong together?"""
    if not left.author_alias or not right.author_alias:
        return True  # old snapshot without sender data: cannot judge, do not accuse
    if _relation(left, right, by_id) or _relation(right, left, by_id):
        return True
    same_topic = left.topic_id == right.topic_id
    gap = abs(_minutes(left, right))
    if same_topic and gap <= 3:
        return True  # an adjacent date/fragment, the established evidence pattern
    return same_topic and _same_author(left, right) and gap <= 60


def _text_supplement_ok(anchor, member, link):
    return (
        link.relation == "reply"
        and (_staff(member) or _same_author(anchor, member))
        and bool(CONTENT_SIGNAL.search(member.text or ""))
        and not NEW_TASK.search(member.text or "")
    )


def _ref(message, *, supplement=False):
    return EvidenceRef(
        telegram_message_id=message.telegram_message_id,
        revision=message.revision,
        fact_paths=["description", SUPPLEMENT_PATH] if supplement else ["description"],
    )


def _with_reason(reasons, reason):
    return reasons if reason in reasons else [*reasons, reason]


def reconcile_validation(validation, messages):
    """Return a validation result with provable supplements attached (idempotent)."""
    if not validation.accepted:
        return validation
    by_id = {row.telegram_message_id: row for row in messages}
    links = link_candidates(messages)
    strong_from = {}
    medium_from = {}
    for link in links:
        target = strong_from if link.strength == STRONG else medium_from
        if link.strength != WEAK:
            target.setdefault(link.anchor, []).append(link)
    actions = Counter(validation.adapter_actions)
    items = list(zip(validation.accepted_ordinals, validation.accepted, strict=True))
    rejected = list(validation.rejected)

    def ids(proposal):
        return {ref.telegram_message_id for ref in proposal.evidence}

    # 1. A separate card that is only a confirmed clarification of another card is merged.
    index = 0
    while index < len(items):
        ordinal, candidate = items[index]
        target = _merge_target(index, candidate, items, by_id, strong_from)
        if target is None:
            index += 1
            continue
        host_index, host = target
        evidence = [*host.evidence, *[r for r in candidate.evidence if r not in host.evidence]]
        description = host.description
        if description and candidate.description and candidate.description not in description:
            description = (description + "\n\nДополнение: " + clean_text(candidate.description))[
                :8000
            ]
        items[host_index] = (
            items[host_index][0],
            host.model_copy(update={"evidence": evidence, "description": description}),
        )
        rejected.append(RejectedItem(proposal_ordinal=ordinal, reason_code="merged_supplement"))
        actions["duplicate_supplement_merged"] += 1
        del items[index]
        if host_index > index:
            host_index -= 1

    # 2-4. Attach strong members, flag doubtful ones, flag foreign sources.
    cited_all = [ids(proposal) for _, proposal in items]
    updated = []
    for position, (ordinal, proposal) in enumerate(items):
        own = ids(proposal)
        evidence = list(proposal.evidence)
        description = proposal.description
        reasons = list(proposal.attention_reasons)
        others = (
            set().union(*(c for k, c in enumerate(cited_all) if k != position))
            if len(cited_all) > 1
            else set()
        )
        queue = sorted(own)
        visited = set()
        while queue:
            anchor_id = queue.pop(0)
            if anchor_id in visited:
                continue
            visited.add(anchor_id)
            anchor = by_id.get(anchor_id)
            if anchor is None:
                continue
            claimants = [k for k, c in enumerate(cited_all) if anchor_id in c]
            for link in strong_from.get(anchor_id, []):
                member = by_id[link.member]
                if link.member in own or link.member in others:
                    continue
                if len(claimants) > 1:
                    if "supplement_ambiguous" not in reasons:
                        reasons.append("supplement_ambiguous")
                        actions["supplement_ambiguous_flagged"] += 1
                    continue
                if not _has_text(member) or link.relation == "album":
                    evidence.append(_ref(member))
                    own.add(link.member)
                    queue.append(link.member)  # siblings of an album join the same card
                    actions["supplement_source_added"] += 1
                elif _text_supplement_ok(anchor, member, link):
                    # The clarification stays a cited source (marked as a supplement reply). Its
                    # raw chat text is never pasted into the description. Only a clarification
                    # that says something new flags the card as awaiting integration; a reply that
                    # repeats the card changes nothing the student sees.
                    evidence.append(_ref(member, supplement=True))
                    own.add(link.member)
                    actions["supplement_source_added"] += 1
                    known = f"{proposal.title or ''}\n{description or ''}"
                    if supplement_adds_information(known, member.text or ""):
                        reasons = _with_reason(reasons, PENDING_INTEGRATION)
                        actions["supplement_pending_integration"] += 1
                elif CONTENT_SIGNAL.search(member.text or ""):
                    reasons = _with_reason(reasons, "possible_missing_supplement")
                    actions["possible_missing_supplement_flagged"] += 1
            for link in medium_from.get(anchor_id, []):
                member = by_id[link.member]
                if (
                    link.member in own
                    or link.member in others
                    or not _has_text(member)
                    or not CONTENT_SIGNAL.search(member.text)
                ):
                    continue
                reasons = _with_reason(reasons, "possible_missing_supplement")
                actions["possible_missing_supplement_flagged"] += 1
        sources = [by_id[i] for i in sorted(own) if i in by_id]
        if len(sources) >= 2 and any(
            not any(_connected(a, b, by_id) for b in sources if b is not a) for a in sources
        ):
            reasons = _with_reason(reasons, "foreign_source_attached")
            actions["foreign_source_flagged"] += 1
        updated.append(
            (
                ordinal,
                proposal.model_copy(
                    update={
                        "evidence": evidence,
                        "description": description,
                        "attention_reasons": reasons,
                    }
                ),
            )
        )
    return validation.model_copy(
        update={
            "accepted": [proposal for _, proposal in updated],
            "accepted_ordinals": [ordinal for ordinal, _ in updated],
            "rejected": rejected,
            "adapter_actions": dict(sorted(actions.items())),
        }
    )


def _merge_target(index, candidate, items, by_id, strong_from):
    """The card a clarification-only proposal belongs to, when every cited message proves it."""
    if candidate.action != "create" or candidate.deadline.at is not None:
        return None
    members = {ref.telegram_message_id for ref in candidate.evidence}
    for host_index, (_, host) in enumerate(items):
        if host_index == index or host.action != "create":
            continue
        host_ids = {ref.telegram_message_id for ref in host.evidence}
        if members & host_ids:
            continue
        if (
            host.semantic_kind != candidate.semantic_kind
            or host.audience != candidate.audience
            or (candidate.subject and host.subject and candidate.subject != host.subject)
        ):
            continue
        proven = True
        for member_id in members:
            member = by_id.get(member_id)
            link = next(
                (
                    row
                    for anchor in host_ids
                    for row in strong_from.get(anchor, [])
                    if row.member == member_id
                ),
                None,
            )
            anchor_message = by_id.get(link.anchor) if link else None
            if (
                member is None
                or link is None
                or not _has_text(member)
                or not _text_supplement_ok(anchor_message, member, link)
            ):
                proven = False
                break
        if proven:
            return host_index, host
    return None
