"""Deterministic last-resort titles built from structured facts, never from a chat fragment.

Used by ``card_editor`` only when the model's own title is unusable. Order: the action clause of
the model's validated description -> the action clause of the cited source text -> a neutral
"clarification needed" title. A candidate is accepted only if it is a *safe* title (an action in
the infinitive or a named work) and not conversational phrasing ("У кого…", "Это…", "Не…").
Nothing here invents a subject, topic or requirement: every word comes from the description or
the source; only the preamble, modal wrapper and delivery condition are removed.
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

# Chat phrasing is never a title: openers that address people, ask questions or comment on the
# conversation instead of naming the work.
CONVERSATIONAL = re.compile(
    r"^(?:у\s+кого|у\s+нас|у\s+меня|если\s+кто|кто(?:[\s-]?(?:нибудь|то|либо))?(?![\wа-яё])|"
    r"кому(?![\wа-яё])|ребят\w*|народ|друзья|коллеги|всем(?![\wа-яё])|короче|привет\w*|"
    r"здравствуй\w*|добр\w+\s+(?:день|вечер|утро)|подскажите|скажите|есть\s+(?:ли|кто)|"
    r"а\s+(?:кто|что|как|где|когда)|можно\s+ли|как(?![\wа-яё])|где(?![\wа-яё])|"
    r"когда(?![\wа-яё])|почему|зачем|что(?![\wа-яё])|это(?![\wа-яё])|вот(?![\wа-яё])|"
    r"там(?![\wа-яё])|тут(?![\wа-яё])|ну(?![\wа-яё])|так(?![\wа-яё])|напоминаю|напоминание|"
    r"не\s|кстати|слушайте|смотрите|внимание|я(?![\wа-яё])|мы(?![\wа-яё])|нам(?![\wа-яё])|"
    r"мне(?![\wа-яё]))",
    re.IGNORECASE,
)
ASSESSMENT_START = re.compile(
    r"^(?:повторн\w+\s+|итогов\w+\s+|промежуточн\w+\s+)?(?:контрольн\w*|самостоятельн\w*|"
    r"проверочн\w*|тест\w*|зач[её]т\w*|экзамен\w*|коллоквиум\w*|лабораторн\w*|практическ\w*|"
    r"опрос\w*|диктант\w*|викторин\w*|вебинар\w*|кт\s?\d|кр\s?\d|пир(?![\wа-яё]))",
    re.IGNORECASE,
)
NOUN_START = re.compile(
    r"^(?:задач\w*|задани\w*|упражнен\w*|номер\w*|параграф\w*|глав\w*|тем[аыу](?![\wа-яё]))",
    re.IGNORECASE,
)
MAX_TITLE = 90


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


def is_conversational_title(title):
    """True for a phrase that talks to the chat instead of naming the work."""
    text = " ".join((title or "").split())
    return not text or "?" in text or text.endswith("…") or CONVERSATIONAL.match(text) is not None


def is_safe_title(title):
    """A rebuilt title must be a plain action or a named work, never a chat fragment."""
    if not title or len(title) < 6 or len(title) > MAX_TITLE or is_conversational_title(title):
        return False
    return bool(
        INFINITIVE.match(title)
        or WORK_NOUNS.match(title)
        or ASSESSMENT_START.match(title)
        or NOUN_START.match(title)
    )


def _candidates(text):
    yield action_clause(text)
    for part in re.split(r"(?<=[.!?])\s+|\n", text):
        if part.strip():
            yield action_clause(part)


def build_title(proposal_description, sources_text, label):
    """Return ``(title, origin)``; origin is one of description/source/insufficient.

    The first *safe* candidate wins; if none is safe the title says that clarification is needed
    instead of presenting a chat fragment as the name of the work.
    """
    for text, origin in ((proposal_description, "description"), (sources_text, "source")):
        for title in dict.fromkeys(item for item in _candidates(text or "") if item):
            if is_safe_title(title):
                return title, origin
    return f"{label}: {INSUFFICIENT}", "insufficient"
