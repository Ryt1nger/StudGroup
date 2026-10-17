"""Deterministic last-resort titles built from structured facts, never from a raw first sentence.

Order used by ``card_editor``: the model's own title (kept when sound) -> the action clause of
the model's validated description -> the action clause of the cited source text -> a short
neutral title that says the data is insufficient. Nothing here invents a subject, topic or
requirement: every word of the result comes from the description or the source, only the
preamble, modal wrapper and delivery condition are removed and a few verbs are normalised.
"""

import re

from studgroup.analysis.text_hygiene import clean_text, clean_title

FILLERS = (
    "ребят",
    "ребята",
    "короче",
    "тут",
    "значит",
    "так",
    "ну",
    "в общем",
    "вобщем",
    "кстати",
    "напоминаю",
    "напоминание",
    "не забудьте",
    "не забываем",
    "внимание",
    "важно",
    "слушайте",
    "смотрите",
    "всем",
    "уважаемые студенты",
    "коллеги",
    "ещё",
    "еще",
    "и",
    "а",
)
MODAL = re.compile(
    r"\b(?:нужно|надо|необходимо|требуется|следует|должны|должен|придётся|придется|"
    r"будем|будет нужно|не забудьте|обязательно)\s+",
    re.IGNORECASE,
)
SUBJECT_PREFIX = re.compile(
    r"^по\s+[а-яёa-z\-]+(?:\s+[а-яёa-z\-]+)?\s*(?=[,:\-–—]|нужно|надо)", re.IGNORECASE
)
COLLECTIVE_VERB = re.compile(
    r"^(?:каждый|каждому|все|всем|вся группа)\s+(?:студент\w*\s+)?"
    r"(?P<verb>делает|делаем|готовит|готовим|пишет|пишем|решает|решаем|выполняет|выполняем|"
    r"находит|находим|выбирает|выбираем|читает|читаем|учит|учим)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
VERB_FORMS = {
    "делает": "подготовить",
    "делаем": "подготовить",
    "готовит": "подготовить",
    "готовим": "подготовить",
    "пишет": "написать",
    "пишем": "написать",
    "решает": "решить",
    "решаем": "решить",
    "выполняет": "выполнить",
    "выполняем": "выполнить",
    "находит": "найти",
    "находим": "найти",
    "выбирает": "выбрать",
    "выбираем": "выбрать",
    "читает": "прочитать",
    "читаем": "прочитать",
    "учит": "выучить",
    "учим": "выучить",
}
WORK_NOUNS = re.compile(
    r"\b(?:презентаци|доклад|реферат|эссе|проект|слайд|схем|таблиц|отч[её]т|конспект|"
    r"сообщени|буклет|постер|плакат|опис|анализ)",
    re.IGNORECASE,
)
DEADLINE_PREFIX = re.compile(
    r"^(?:к|на|до)\s+(?:следующ\w+|ближайш\w+|завтра\w*|сегодня\w*)(?:\s+(?:паре|занятию|неделе|"
    r"лекции|семинару|уроку))?\s*(?=[а-яё])",
    re.IGNORECASE,
)
ENUM_MARKER = re.compile(r"^\s*(?:\d{1,2}[).]|[-•*–—])\s+")
INFINITIVE = re.compile(r"^[а-яё]+(?:ть|ти|чь)(?:ся)?\b", re.IGNORECASE)
CONDITION_SPLIT = re.compile(
    r"\s*(?:[,;!?\n]\s|[.!?](?=\s|$)|;|\n|\s(?:а потом|потом|затем|после чего|и сдать|и отправить|"
    r"и прислать|сдать|сдаём|сдаем|сдавать|отправить|прислать|до|к)\s)",
    re.IGNORECASE,
)
TYPOS = (
    (re.compile(r"презинтац", re.IGNORECASE), "презентац"),
    (re.compile(r"презентци", re.IGNORECASE), "презентаци"),
    (re.compile(r"рефарат|рефорат", re.IGNORECASE), "реферат"),
    (re.compile(r"докдад|даклад", re.IGNORECASE), "доклад"),
    (re.compile(r"конспек(?=\b)", re.IGNORECASE), "конспект"),
    (re.compile(r"\bпро\s+(?=петр)", re.IGNORECASE), "про "),
)
INSUFFICIENT = "уточнить по сообщению в чате"


def _fix_typos(text):
    for pattern, replacement in TYPOS:
        text = pattern.sub(replacement, text)
    return text


def _strip_fillers(text):
    changed = True
    while changed:
        changed = False
        lowered = text.casefold()
        for filler in sorted(FILLERS, key=len, reverse=True):
            if re.match(rf"{re.escape(filler)}(?![\wа-яё])[\s,:;.!—–-]*", lowered):
                match = re.match(rf"{re.escape(filler)}(?![\wа-яё])[\s,:;.!—–-]*", lowered)
                text = text[match.end() :]
                changed = True
                break
    return text.strip()


def _verb_normalised(clause):
    first, _, rest = clause.partition(" ")
    if first.casefold() in {"сделать", "создать", "оформить"} and WORK_NOUNS.search(rest):
        return "Подготовить " + rest
    return clause[:1].upper() + clause[1:]


def action_clause(text):
    """Action + object (+ topic) from a message or description; ``None`` if nothing usable."""
    cleaned = _fix_typos(clean_text(text))
    if not cleaned:
        return None
    lines = [line for line in cleaned.splitlines() if line.strip()]
    items = [line for line in lines if ENUM_MARKER.match(line)]
    announce = next((line for line in lines if not ENUM_MARKER.match(line)), None)
    if announce is None and len(items) >= 2:
        # Only a list: name the first items instead of the first bullet alone.
        parts = [action_clause(ENUM_MARKER.sub("", line)) for line in items[:3]]
        parts = [part[:1].lower() + part[1:] for part in parts if part]
        if parts:
            return clean_title(
                (parts[0][:1].upper() + parts[0][1:])
                + "".join(
                    f"{' и ' if index == len(parts) - 1 else ', '}{part}"
                    for index, part in enumerate(parts[1:], 1)
                )
            )
        return None
    sentence = _strip_fillers(" ".join((announce or lines[0]).split()))
    sentence = ENUM_MARKER.sub("", sentence)
    sentence = DEADLINE_PREFIX.sub("", sentence)
    sentence = SUBJECT_PREFIX.sub("", sentence).strip(" ,:-–—")
    collective = COLLECTIVE_VERB.match(sentence)
    if collective:
        sentence = f"{VERB_FORMS[collective['verb'].casefold()]} {collective['rest']}"
    else:
        modal = MODAL.search(sentence)
        if modal:
            sentence = sentence[modal.end() :]
    sentence = _strip_fillers(sentence)
    clause = CONDITION_SPLIT.split(sentence, maxsplit=1)[0].strip(" ,:-–—")
    # A leading "по <предмету>" fragment that survived is not part of the action.
    clause = (
        re.sub(r"^по\s+[а-яёa-z\-]+\s+", "", clause, flags=re.IGNORECASE)
        if not INFINITIVE.match(clause) and WORK_NOUNS.search(clause) is None
        else clause
    )
    if len(clause) < 6 or not re.search(r"[а-яёa-z]{3}", clause, re.IGNORECASE):
        return None
    title = clean_title(_verb_normalised(clause))
    return title if len(title) >= 6 else None


def build_title(proposal_description, sources_text, label):
    """Return ``(title, origin)``; origin is one of structured/source/insufficient."""
    for text, origin in ((proposal_description, "description"), (sources_text, "source")):
        title = action_clause(text or "")
        if title:
            return title, origin
    return f"{label}: {INSUFFICIENT}", "insufficient"
