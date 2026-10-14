"""Owner-only text rendering of accepted shadow cards with their cited source messages."""

MAX_CHUNK = 3500
SOURCE_TEXT_LIMIT = 500


def telegram_link(chat_id, message_id):
    text = str(chat_id)
    if text.startswith("-100") and len(text) > 4:
        return f"https://t.me/c/{text[4:]}/{message_id}"
    return None


def _clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_card(index, card, chat_id):
    deadline = card["deadline_at"]
    when = (
        "срок не указан"
        if deadline is None
        else f"срок {deadline:%d.%m.%Y %H:%M} ({'только дата' if card['deadline_date_only'] else 'с временем'}, {card['deadline_basis']})"
    )
    lines = [
        f"#{index} [{card['kind']}] {card['subject'] or 'без предмета'} — {card['title']}",
        _clip(card["description"], 400),
        f"{when}; аудитория: {card['audience'].get('scope')}; "
        f"показ: {card['visibility']}"
        + (f" ({', '.join(card['attention_reasons'])})" if card["attention_reasons"] else ""),
        "Источники:",
    ]
    for source in card["sources"]:
        link = telegram_link(chat_id, source["telegram_message_id"])
        stamp = source["message_date"]
        lines.append(
            f"• №{source['telegram_message_id']} {stamp:%d.%m %H:%M}: "
            f"{_clip(source['text'], SOURCE_TEXT_LIMIT)}" + (f"\n  {link}" if link else "")
        )
    return "\n".join(lines)


def render_cards(cards, chat_id):
    """Chunks that fit one Telegram message; never splits a card across chunks."""
    chunks, current = [], ""
    for index, card in enumerate(cards, 1):
        block = render_card(index, card, chat_id)[:MAX_CHUNK]
        if current and len(current) + len(block) + 2 > MAX_CHUNK:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks
