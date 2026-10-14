"""Evidence checks and deterministic fallback for AI-authored display fields."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from studgroup.academic_context import has_date_cue
from studgroup.analysis.contracts import ProposedAcademicItem, SourceMessage
from studgroup.analysis.output_adapter import explicit_source_dates, mentions_weekday
from studgroup.analysis.text_hygiene import (
    clean_text,
    clean_title,
    is_service_subject,
    neutral_title,
)

GENERIC_TITLE_PATTERN = re.compile(
    r"^(?:учебное|домашнее)\s+задание(?:\s+по\s+(?:предмету\s+)?[«\"]?[\w\s-]{0,40}[»\"]?)?$|"
    r"^(?:(?:доклад|презентация|задание|орг)\s*)+$"
)
KIND_LABELS = {"homework": "Задание"}
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
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
DEADLINE_LABELS = {
    "test": "Тест",
    "control_point": "Контрольная точка",
    "assessment": "Оценочная работа",
    "credit": "Зачёт",
    "exam": "Экзамен",
    "other_academic_deadline": "Учебный дедлайн",
}
CRITICAL_FACT_STEMS = {
    "презентац",
    "реферат",
    "доклад",
    "конспект",
    "эссе",
    "задач",
    "упражнен",
    "номер",
    "страниц",
    "лист",
    "тест",
    "зачет",
    "экзамен",
    "контрольн",
}


class EditedCard(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=8000)
    visibility: Literal["normal", "headman_attention", "held"]
    attention_reasons: list[str] = Field(default_factory=list)
    used_fallback: bool = False
    subject: str | None = None


def normalized(value):
    return " ".join(value.casefold().split())


def phrase_supported(value, evidence_text):
    value_tokens = re.findall(r"[a-zа-яё]+", normalized(value))
    evidence_tokens = re.findall(r"[a-zа-яё]+", evidence_text)
    return bool(value_tokens) and all(
        any(token[:5] == candidate[:5] for candidate in evidence_tokens)
        for token in value_tokens
    )


def cited_sources(proposal, messages):
    by_key = {(row.telegram_message_id, row.revision): row for row in messages}
    rows = []
    for evidence in proposal.evidence:
        source = by_key.get((evidence.telegram_message_id, evidence.revision))
        if source is None:
            raise ValueError("card_evidence_unavailable")
        if source not in rows:
            rows.append(source)
    return rows


def unsupported_numbers(display_text, sources):
    displayed = set(NUMBER.findall(display_text))
    evidenced = {number for source in sources for number in NUMBER.findall(source.text)}
    return displayed - evidenced


def primary_display_valid(proposal, sources, verified_subjects):
    if not proposal.title or not proposal.description:
        return False, "missing_display_text"
    title = normalized(proposal.title)
    if title in GENERIC_TITLES or GENERIC_TITLE_PATTERN.match(title) or len(title) < 6:
        return False, "generic_title"
    source_texts = {normalized(source.text) for source in sources if source.text}
    if title in source_texts and len(title) > 120:
        return False, "copied_chat_title"
    if unsupported_numbers(f"{proposal.title}\n{proposal.description}", sources):
        return False, "unsupported_numeric_fact"
    evidence_text = normalized("\n".join(source.text for source in sources))
    display_text = normalized(f"{proposal.title}\n{proposal.description}")
    if any(stem in display_text and stem not in evidence_text for stem in CRITICAL_FACT_STEMS):
        return False, "unsupported_critical_fact"
    if proposal.subject and (
        not phrase_supported(proposal.subject, evidence_text)
        and normalized(proposal.subject) not in {normalized(item) for item in verified_subjects}
    ):
        return False, "unsupported_subject"
    if proposal.deadline.basis == "explicit" and not any(
        has_date_cue(source.text)
        or explicit_source_dates(source.text)
        or mentions_weekday(source.text)
        for source in sources
    ):
        return False, "unsupported_explicit_deadline"
    return True, None


def fallback_display(proposal, sources, include_subject):
    """Neutral display text from the cleaned source; nothing is inferred or invented."""
    label = (
        KIND_LABELS["homework"]
        if proposal.semantic_kind == "homework"
        else DEADLINE_LABELS[proposal.semantic_kind]
    )
    primary = next((source.text for source in sources if (source.text or "").strip()), "")
    title = neutral_title(primary, label)
    cleaned = [clean_text(source.text) for source in sources if (source.text or "").strip()]
    cleaned = [text for text in cleaned if text]
    description = "\n".join(f"• {text}" if len(cleaned) > 1 else text for text in cleaned)
    if not description:
        description = "Содержание находится в приложенном исходном сообщении."
    return title, description[:8000]


def _scrub(proposal):
    """Service tags, greetings and spacing out of AI-written display text (facts untouched)."""
    title = clean_title(proposal.title or "")
    description = clean_text(proposal.description or "")
    return proposal.model_copy(update={"title": title, "description": description})


def edit_card(
    proposal: ProposedAcademicItem,
    messages: list[SourceMessage],
    *,
    verified_subjects=(),
):
    sources = cited_sources(proposal, messages)
    proposal = _scrub(proposal)
    reasons = list(dict.fromkeys(proposal.attention_reasons))
    subject = None if is_service_subject(proposal.subject) else proposal.subject
    if subject != proposal.subject:
        proposal = proposal.model_copy(update={"subject": None})
        reasons.append("service_tag_not_subject")
    evidence_text = normalized("\n".join(source.text for source in sources))
    subject_supported = not proposal.subject or (
        phrase_supported(proposal.subject, evidence_text)
        or normalized(proposal.subject) in {normalized(item) for item in verified_subjects}
    )
    if proposal.audience.scope == "personal":
        return EditedCard(
            title=proposal.title or "Личное поручение",
            description=proposal.description or "Требуется индивидуальная проверка.",
            visibility="held",
            attention_reasons=[*reasons, "personal_not_group_publishable"],
            subject=subject,
        )
    if SERIOUS_REASONS & set(reasons):
        title, description = fallback_display(proposal, sources, subject_supported)
        return EditedCard(
            title=title,
            description=description,
            visibility="held",
            attention_reasons=reasons,
            used_fallback=True,
            subject=subject if subject_supported else None,
        )
    valid, invalid_reason = primary_display_valid(proposal, sources, verified_subjects)
    if valid:
        return EditedCard(
            title=proposal.title,
            description=proposal.description,
            visibility="headman_attention" if reasons else "normal",
            attention_reasons=reasons,
            subject=subject,
        )
    title, description = fallback_display(proposal, sources, subject_supported)
    return EditedCard(
        title=title,
        description=description,
        visibility="headman_attention",
        attention_reasons=[*reasons, invalid_reason],
        used_fallback=True,
        subject=subject if subject_supported else None,
    )
