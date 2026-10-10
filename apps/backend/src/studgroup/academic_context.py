"""Bounded evidence retrieval: direct replies first, then same-subject date context."""

import re
from datetime import timedelta


def tags(text):
    return set(re.findall(r"#[\w]+", text.casefold()))


def homework_range_applies(task_text, clarification):
    """Strict page + exercise-range + single-subject evidence, not generic proximity."""
    subject = tags(task_text)
    if len(subject) != 1 or tags(clarification) != subject:
        return False
    number = re.search(r"(?:^|\n|номер\w*\s+|№\s*)(\d{1,2})(?:[.,]\d+)?\b", task_text)
    page = re.search(r"стр\.?\s*(\d{1,3})", task_text, re.IGNORECASE)
    numbers = re.search(
        r"(?:с\s+)?(\d{1,2})\s+по\s+(\d{1,2})\s+номер", clarification, re.IGNORECASE
    )
    pages = re.search(r"стр\.?\s*(\d{1,3})\s*[-–—]\s*(\d{1,3})", clarification, re.IGNORECASE)
    return bool(
        number
        and page
        and numbers
        and pages
        and int(numbers[1]) <= int(number[1]) <= int(numbers[2])
        and int(pages[1]) <= int(page[1]) <= int(pages[2])
    )


def has_date_cue(text):
    return bool(
        re.search(
            r"завтра|послезавтра|сегодня|(?:следующ|ближайш|конц[ае])\w*\s+(?:недел|сред|четверг|пятниц|суббот|воскрес|понедель|вторник)|"
            r"(?:до|к|на|в)\s+(?:\d{1,2}[./]\d{1,2}|\d{1,2}\s+[а-я]+|понедельник|вторник|сред[уы]|четверг|пятниц[уы]|суббот[уы]|воскресенье)",
            text,
            re.IGNORECASE,
        )
    )


def build_context(messages, target_id, max_bytes=13000, *, neighborhood=0):
    by_id = {message.message_id: message for message in messages}
    target = by_id[target_id]
    selected = {target_id}
    chronological = sorted(messages, key=lambda m: (m.message_date, m.message_id))
    position = next(i for i, m in enumerate(chronological) if m.message_id == target_id)
    surrounding = {
        m.message_id
        for m in chronological[max(0, position - neighborhood) : position + neighborhood + 1]
    }
    # Generic pinned topic IDs are absent from the export. Never fabricate their contents.
    parent = by_id.get(target.reply_to_message_id)
    if parent and parent.text:
        selected.add(parent.message_id)
    for _ in range(2):
        selected.update(
            m.message_id for m in messages if m.reply_to_message_id in selected and m.text
        )
    subject_tags = tags(target.text)
    ranked = []
    for message in messages:
        if not message.text or message.message_id == target_id:
            continue
        direct = message.message_id in selected
        same_subject = bool(subject_tags & tags(message.text))
        nearby = abs(message.message_date - target.message_date) <= timedelta(days=7)
        correction = abs(message.message_date - target.message_date) <= timedelta(
            minutes=3
        ) and re.search(r"оказывается|это не кт|перенес|отмен", message.text, re.IGNORECASE)
        # Adjacent date fragments are evidence candidates, not automatic inherited dates.
        # The provider must cite supporting IDs and match the task/subject before using them.
        date_fragment = abs(message.message_date - target.message_date) <= timedelta(
            minutes=3
        ) and (has_date_cue(target.text) or has_date_cue(message.text))
        if (
            direct
            or correction
            or date_fragment
            or (same_subject and nearby)
            or message.message_id in surrounding
        ):
            ranked.append((not direct, abs(message.message_date - target.message_date), message))
    used = len(target.text.encode()) + 300
    chosen = [target]
    for _, _, message in sorted(ranked, key=lambda row: (row[0], row[1], row[2].message_id)):
        size = len(message.text.encode()) + 300
        if used + size > max_bytes or len(chosen) >= 25:
            continue
        used += size
        chosen.append(message)
    return [
        {
            "message_id": m.message_id,
            "message_date": m.message_date.isoformat(),
            "reply_to": m.reply_to_message_id,
            "topic_id": getattr(m, "message_thread_id", None),
            "topic_name": getattr(m, "topic_name", None),
            "text": m.text,
            "is_target": m.message_id == target_id,
        }
        for m in sorted(chosen, key=lambda m: (m.message_date, m.message_id))
    ]
