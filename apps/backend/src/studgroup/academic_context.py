"""Bounded evidence retrieval: direct replies first, then same-subject date context."""

import re
from datetime import timedelta


def tags(text):
    return set(re.findall(r"#[\w]+", text.casefold()))


def build_context(messages, target_id, max_bytes=13000):
    by_id = {message.message_id: message for message in messages}
    target = by_id[target_id]
    selected = {target_id}
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
        if direct or correction or (same_subject and nearby):
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
            "text": m.text,
            "is_target": m.message_id == target_id,
        }
        for m in sorted(chosen, key=lambda m: (m.message_date, m.message_id))
    ]
