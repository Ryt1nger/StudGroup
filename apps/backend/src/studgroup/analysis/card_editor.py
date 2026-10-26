"""Evidence checks and per-field fallback for AI-authored display fields.

Title, description and subject are validated *independently*: a problem in one field never
replaces another field that is sound. A model title or description is replaced only for a concrete,
named reason (conversational phrase, generic wording, unsupported number/date/audience/concept).
Rebuilt text is never assembled from raw chat messages: a rebuilt title must be a safe action or
work title, a rebuilt description is a neutral "pending" marker until the AI re-reads the item.
"""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studgroup.academic_context import has_date_cue
from studgroup.analysis.contracts import ProposedAcademicItem, SourceMessage
from studgroup.analysis.output_adapter import explicit_source_dates, mentions_weekday
from studgroup.analysis.text_hygiene import clean_text, clean_title, is_service_subject
from studgroup.analysis.title_builder import build_title, is_conversational_title

GENERIC_TITLE_PATTERN = re.compile(
    r"^(?:учебное|домашнее)\s+задание(?:\s+по\s+(?:предмету\s+)?[«\"]?[\w\s-]{0,40}[»\"]?)?$|"
    r"^(?:(?:доклад|презентация|задание|орг)\s*)+$"
)
KIND_LABELS = {"homework": "Задание"}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
ENUMERATION_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+|(?<!\S)\d{1,2}\)(?=\s)")
MAX_TITLE_CHARS = 90
GENERIC_TITLES = {
    "домашнее задание",
    "домашка",
    "задание",
    "дедлайн",
    "учебное событие",
}
SERIOUS_REASONS = {
    "material_conflict",
    "model_material_doubt",
    "conflicting_deadlines",
    "deadline_before_source",
    "unconfirmed_change",
}
# ``foreign_source_attached`` (a cited message with no structural tie to the others) used to be
# serious and held the card. Owner decision (20 Oct 2026): the card is published for the headman
# with the flag instead, because real chats discuss one task across unlinked messages and holding
# lost cards the reviewed demo shows. The flag stays on the card so the headman can check it.
DEADLINE_LABELS = {
    "test": "Тест",
    "control_point": "Контрольная точка",
    "assessment": "Оценочная работа",
    "credit": "Зачёт",
    "exam": "Экзамен",
    "other_academic_deadline": "Учебный дедлайн",
}
# Shown instead of a description the model could not support. It states no fact about the task.
DESCRIPTION_PENDING = "Описание уточняется: подробности в исходном сообщении чата."
DESCRIPTION_ATTACHMENT_ONLY = "Содержание находится в приложенном исходном сообщении."

# A work/concept named in the display text must be named by the evidence too, but any word of the
# same family counts (a paraphrase such as "выступление" for "доклад" or "КТ3" for "контрольная
# точка" is not an invented fact). Left: how the concept shows up in display text; right: how it
# may show up in the cited messages. Both sides are matched on casefolded, ё-folded text.
CONCEPT_FAMILIES = {
    "presentation": (
        r"презентац|слайд",
        r"презентац|слайд|\bпрезу?\b|преза|презк|powerpoint|\bppt",
    ),
    "report": (r"доклад", r"доклад|выступлен|сообщен|спич"),
    "abstract": (r"реферат", r"реферат"),
    "essay": (r"эссе|сочинен", r"эссе|сочинен"),
    "notes": (r"конспект", r"конспект"),
    "exercise": (r"задач|упражнен|номер", r"задач|упражнен|номер|задани|пример|№|\bупр\b"),
    "page": (r"страниц", r"страниц|\bстр\b|\bс\.\s*\d"),
    "sheet": (r"лист", r"лист"),
    "test": (r"\bтест", r"\bтест|опрос|квиз|\bсдо\b|тестирован"),
    "credit": (r"зачет", r"зачет"),
    "exam": (r"экзамен", r"экзамен"),
    "control": (
        r"контрольн",
        r"контрол|самостоятельн|проверочн|\bк\.?\s?т\.?\s?\d|\bкт\b|\bкр\b|\bкр\d",
    ),
}
CONCEPTS = {
    name: (re.compile(shown), re.compile(allowed))
    for name, (shown, allowed) in CONCEPT_FAMILIES.items()
}
DATE_WORDS = re.compile(
    r"понедельник|вторник|\bсред[аеуы]\b|четверг|пятниц|суббот|воскресень|выходн|"
    r"января|февраля|марта|апреля|\bмая\b|июня|июля|августа|сентября|октября|ноября|декабря|"
    r"послезавтра|завтра|сегодня|вчера",
)
AUDIENCE_WORDS = re.compile(r"подгрупп|бригад")


class EditedCard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=8000)
    visibility: Literal["normal", "headman_attention", "held"]
    attention_reasons: list[str] = Field(default_factory=list)
    used_fallback: bool = False
    subject: str | None = None
    # Which display fields were replaced ("title", "description"); empty when the model's own
    # text was kept.
    fallback_fields: list[str] = Field(default_factory=list)


def normalized(value):
    return " ".join(value.casefold().replace("ё", "е").split())


def phrase_supported(value, evidence_text):
    value_tokens = re.findall(r"[a-zа-яё]+", normalized(value))
    evidence_tokens = re.findall(r"[a-zа-яё]+", evidence_text)
    return bool(value_tokens) and all(
        any(token[:5] == candidate[:5] for candidate in evidence_tokens) for token in value_tokens
    )


def cited_sources(proposal, messages):
    by_key = {(row.telegram_message_id, row.revision): row for row in messages}
    rows = []
    for evidence in proposal.evidence:
        source = by_key.get((evidence.telegram_message_id, evidence.revision))
        if source is None:
            raise ValueError("card_evidence_unavailable")
        if source not in rows:
            if source.image_facts:
                source = source.model_copy(update={"text": "\n".join([source.text, *source.image_facts])})
            rows.append(source)
    return rows


def _numbers(text):
    return set(NUMBER.findall(ENUMERATION_MARKER.sub("", text)))


def unsupported_numbers(display_text, sources):
    evidenced = {number for source in sources for number in NUMBER.findall(source.text)}
    return _numbers(display_text) - evidenced


def _date_stems(text):
    return {match.group(0)[:4] for match in DATE_WORDS.finditer(normalized(text))}


def facts_problem(text, sources, evidence_text, audience_scope):
    """First unsupported material fact in a display text, or ``None``.

    Numbers, calendar words, subgroup wording and named works/concepts must come from the cited
    messages (a synonym of the same concept family counts). Plain rewording is not a fact.
    """
    if unsupported_numbers(text, sources):
        return "unsupported_numeric_fact"
    shown = normalized(text)
    for shown_rx, allowed_rx in CONCEPTS.values():
        if shown_rx.search(shown) and not allowed_rx.search(evidence_text):
            return "unsupported_critical_fact"
    if _date_stems(text) - _date_stems(" ".join(source.text for source in sources)):
        return "unsupported_date_fact"
    if (
        audience_scope != "subgroup"
        and AUDIENCE_WORDS.search(shown)
        and not AUDIENCE_WORDS.search(evidence_text)
    ):
        return "unsupported_audience_fact"
    return None


SUPPLEMENT_MIN_NEW_WORDS = 2


def _content_stems(text):
    return {word[:5] for word in re.findall(r"[a-zа-я]{4,}", normalized(text))}


def supplement_adds_information(known_text, supplement_text):
    """Does a clarification say something the card does not already say?

    A number, a calendar word or a named work/concept the card lacks, or several new content words,
    count as new information. A reply that only repeats or confirms the card does not. The check
    is deliberately cheap and errs towards "new": a missed integration is worse than a flag.
    """
    if _numbers(supplement_text) - _numbers(known_text):
        return True
    if _date_stems(supplement_text) - _date_stems(known_text):
        return True
    shown_known, shown_new = normalized(known_text), normalized(supplement_text)
    for shown_rx, allowed_rx in CONCEPTS.values():
        if (shown_rx.search(shown_new) or allowed_rx.search(shown_new)) and not (
            shown_rx.search(shown_known) or allowed_rx.search(shown_known)
        ):
            return True
    return (
        len(_content_stems(supplement_text) - _content_stems(known_text))
        >= SUPPLEMENT_MIN_NEW_WORDS
    )


def title_problem(title, raw_length, sources, evidence_text, audience_scope):
    if not title:
        return "missing_display_text"
    # ``clean_title`` cut an overlong title and appended "…": judge the title without that mark.
    cut = raw_length > MAX_TITLE_CHARS
    probe = title.rstrip("…") if cut else title
    lowered = normalized(probe)
    if is_conversational_title(probe):
        return "noisy_title"
    if lowered in GENERIC_TITLES or GENERIC_TITLE_PATTERN.match(lowered) or len(lowered) < 6:
        return "generic_title"
    if cut:
        return "title_too_long"
    return facts_problem(title, sources, evidence_text, audience_scope)


def description_problem(description, sources, evidence_text, audience_scope):
    if not description:
        return "missing_display_text"
    return facts_problem(description, sources, evidence_text, audience_scope)


def _subject_supported(subject, evidence_text, verified_subjects, catalog_confirmed=False):
    # A catalog-confirmed subject needs no literal mention: "матан" in the chat is the catalog's
    # «Математический анализ». Everything else keeps the text rule.
    return (
        not subject
        or catalog_confirmed
        or (
            phrase_supported(subject, evidence_text)
            or normalized(subject) in {normalized(item) for item in verified_subjects}
        )
    )


def _scrub(proposal):
    """Service tags, greetings and spacing out of AI-written display text (facts untouched)."""
    title = clean_title(proposal.title or "")
    description = clean_text(proposal.description or "")
    return proposal.model_copy(update={"title": title, "description": description})


def _salvage_description(description, sources, evidence_text, audience_scope):
    """The sentences of a model description that are individually supported, or ``None``.

    Only the model's own sentences are kept; an unsupported one is dropped, nothing is added.
    """
    parts = [
        part.strip() for part in re.split(r"(?<=[.!?])\s+|\n", description or "") if part.strip()
    ]
    kept = [
        part for part in parts if not facts_problem(part, sources, evidence_text, audience_scope)
    ]
    if not kept or len(kept) == len(parts):
        return None
    return ("\n" if "\n" in description else " ").join(kept)


def _fallback_description(sources):
    has_text = any((source.text or "").strip() for source in sources)
    return DESCRIPTION_PENDING if has_text else DESCRIPTION_ATTACHMENT_ONLY


def edit_card(
    proposal: ProposedAcademicItem,
    messages: list[SourceMessage],
    *,
    verified_subjects=(),
):
    sources = cited_sources(proposal, messages)
    raw_title_length = len(" ".join(clean_text(proposal.title or "").split()))
    proposal = _scrub(proposal)
    reasons = list(dict.fromkeys(proposal.attention_reasons))
    subject = None if is_service_subject(proposal.subject) else proposal.subject
    if subject != proposal.subject:
        proposal = proposal.model_copy(update={"subject": None})
        reasons.append("service_tag_not_subject")
    evidence_text = normalized("\n".join(source.text for source in sources))
    subject_supported = _subject_supported(
        proposal.subject,
        evidence_text,
        verified_subjects,
        catalog_confirmed=proposal.subject_source == "catalog",
    )
    # A subject problem is a subject problem: it is reported and the subject is dropped, but the
    # title and description are judged on their own.
    if not subject_supported:
        reasons.append("unsupported_subject")
    shown_subject = subject if subject_supported else None
    if proposal.audience.scope == "personal":
        return EditedCard(
            title=proposal.title or "Личное поручение",
            description=proposal.description or "Требуется индивидуальная проверка.",
            visibility="held",
            attention_reasons=[*reasons, "personal_not_group_publishable"],
            subject=shown_subject,
        )
    scope = proposal.audience.scope
    description_issue = description_problem(proposal.description, sources, evidence_text, scope)
    title_issue = title_problem(proposal.title, raw_title_length, sources, evidence_text, scope)
    fallback_fields = []
    title, description = proposal.title, proposal.description
    if title_issue:
        label = (
            KIND_LABELS["homework"]
            if proposal.semantic_kind == "homework"
            else DEADLINE_LABELS[proposal.semantic_kind]
        )
        source_text = "\n".join(source.text for source in sources if (source.text or "").strip())
        rebuilt, origin = build_title(
            None if description_issue else proposal.description, source_text, label
        )
        # A long but otherwise sound model title beats "needs clarification": it is kept as is and
        # raises no flag (a card must not be marked unconfirmed merely for being wordy).
        if origin == "insufficient" and title_issue == "title_too_long" and title:
            pass
        else:
            reasons.append(title_issue)
            title = rebuilt
            fallback_fields.append("title")
            if origin == "insufficient":
                reasons.append("title_insufficient_data")
    if description_issue:
        salvaged = _salvage_description(proposal.description, sources, evidence_text, scope)
        fallback_fields.append("description")
        if salvaged:
            description = salvaged
            reasons.extend([description_issue, "description_trimmed"])
        else:
            description = _fallback_description(sources)
            reasons.extend([description_issue, "description_pending_ai"])
    # The deadline is its own field: an "explicit" date the messages never state is flagged, and
    # the display texts are left alone.
    if proposal.deadline.basis == "explicit" and not any(
        has_date_cue(source.text)
        or explicit_source_dates(source.text)
        or mentions_weekday(source.text)
        for source in sources
    ):
        reasons.append("unsupported_explicit_deadline")
    reasons = list(dict.fromkeys(reasons))
    if SERIOUS_REASONS & set(reasons):
        visibility = "held"
    elif fallback_fields or reasons:
        visibility = "headman_attention"
    else:
        visibility = "normal"
    return EditedCard(
        title=title,
        description=description[:8000],
        visibility=visibility,
        attention_reasons=reasons,
        used_fallback=bool(fallback_fields),
        subject=shown_subject,
        fallback_fields=fallback_fields,
    )
