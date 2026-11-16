"""Conservative text-only release gate for image-blocked messages.

This is a narrow allowlist, NOT a generic classifier. If any referenced image,
task statement, or parent could supply essential academic facts, wait for it.
"""

import re

_SOURCE_DEPENDENCY = re.compile(
    r"фото|фотк|скрин|картин|изображ|прикрепл|вложен|на лист|"
    r"на доске|в файл|в документ|показан|в таблиц|см\.|смотри|"
    r"вот это|этот материал|все задачи|задани[яе] [0-9]|"
    r"отсюда|выше|в сообщени|по картинк",
    re.IGNORECASE,
)
_ACTION_WITH_DATE = re.compile(
    r"(?:контрольн|экзамен|зач[её]т|коллоквиум|перенос|сдач|"
    r"сдать|дедлайн|семинар|пар[аыу]|лекци|тест|"
    r"домашн(?:ее|его) задан)",
    re.IGNORECASE,
)
_DATE_OR_TIME = re.compile(
    r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b|"
    r"(?:понедельник|вторник|сред[аыу]|четверг|пятниц|суббот|воскресень|"
    r"сегодня|завтра|послезавтра|недел|ноябр|декабр|январ|феврал|март|апрел|"
    r"ма[йяе]|июн|июл|август|сентябр|октябр)",
    re.IGNORECASE,
)


def independently_actionable_text(text: str | None, *, reply_to: int | None = None) -> bool:
    """Permit only self-contained dated academic actions, never short captions."""
    if not isinstance(text, str) or len(text.strip()) < 45 or len(text) > 5000:
        return False
    if reply_to is not None or _SOURCE_DEPENDENCY.search(text):
        return False
    return bool(_ACTION_WITH_DATE.search(text) and _DATE_OR_TIME.search(text))
