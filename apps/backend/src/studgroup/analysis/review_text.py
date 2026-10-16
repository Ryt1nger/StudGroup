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


REASON_TEXT = {
    "relative_deadline_unresolved": "срок задан относительно пары — дата не подтверждена",
    "deadline_not_in_source": "дата не найдена в исходном тексте",
    "conflicting_deadlines": "в сообщениях разные даты",
    "material_conflict": "противоречие в материалах",
    "model_material_doubt": "серьёзное сомнение, нужна проверка",
    "deadline_before_source": "срок раньше сообщения",
    "unconfirmed_change": "похоже на изменение/перенос уже созданного задания — подтвердить вручную",
    "personal_not_group_publishable": "личное поручение — не публикуется группе",
    "service_tag_not_subject": "служебный тег не считается предметом",
    "unsupported_subject": "предмет не подтверждён сообщениями",
    "subject_not_in_catalog": "предмета нет в списке предметов группы — не назначен",
    "subject_id_unknown": "модель назвала несуществующий id предмета — не принят",
    "subject_ambiguous": "название подходит к нескольким предметам — не назначен",
    "title_insufficient_data": "данных для точного заголовка недостаточно — нейтральный заголовок",
    "noisy_title": "заголовок модели был фразой из чата — перестроен",
    "foreign_source_attached": "к карточке присоединено несвязанное сообщение — проверить",
    "possible_missing_supplement": "рядом есть возможное дополнение, не включённое в карточку",
    "supplement_ambiguous": "дополнение подходит к нескольким заданиям — не присоединено",
    "generic_title": "заголовок модели слишком общий — взят нейтральный",
    "unsupported_numeric_fact": "в тексте есть число, которого нет в источнике",
    "unsupported_critical_fact": "в тексте есть факт, которого нет в источнике",
    "unsupported_explicit_deadline": "срок помечен явным без подтверждения в тексте",
    "unsupported_date_fact": "в тексте есть день или дата, которых нет в источнике",
    "unsupported_audience_fact": "в тексте есть уточнение аудитории, которого нет в источнике",
    "title_too_long": "заголовок модели слишком длинный",
    "description_trimmed": "из описания убрано то, чего нет в сообщениях",
    "description_pending_ai": "новое описание не подтверждено сообщениями — показано прежнее или нейтральное, ждёт повторного разбора",
    "supplement_pending_integration": "в чате есть уточнение, которого нет в описании — оно добавлено источником, описание может быть неполным",
    "missing_display_text": "модель не дала заголовок или описание",
}
ROLE_TEXT = {"headman": "староста", "deputy": "заместитель", "student": "студент"}
PREVIEW_DESCRIPTION_LIMIT = 700
REVISION_ACTIONS = {"create": "создана", "update": "дополнена или изменена", "cancel": "отменена"}


def _reasons(codes):
    return "; ".join(REASON_TEXT.get(code, code) for code in codes)


def render_preview_card(index, card, chat_id):
    audience = card["audience"]
    audience_text = {
        "group": "вся группа",
        "subgroup": f"подгруппа {audience.get('label') or ''}".strip(),
        "personal": "личное поручение",
    }.get(audience.get("scope"), "аудитория неизвестна")
    lines = [
        f"━ №{index} · {card['type']} · {card['subject'] or 'предмет не указан'}",
        card["title"],
        _clip(card["description"], PREVIEW_DESCRIPTION_LIMIT)
        if "\n" not in card["description"]
        else "\n".join(card["description"].splitlines()[:12])[:PREVIEW_DESCRIPTION_LIMIT],
        f"Срок: {card['when']} ({card['trust']}){' — уже прошёл' if card['expired'] else ''}",
        f"Аудитория: {audience_text}",
        f"Статус: {card['status']}"
        + (f"\nК проверке: {_reasons(card['reasons'])}" if card["reasons"] else ""),
    ]
    from studgroup.analysis.card_materials import group_counts, summary_label

    if card["materials"]:
        lines.append(f"Материалы: {summary_label(card['materials'])}")
        for item in card["materials"][:6]:
            target = (
                item.get("url") or item.get("telegram_link") or f"сообщение №{item['message_id']}"
            )
            lines.append(f"  • {item['title']} — {target}")
        lines.append("  (содержимое файлов C+ не читал)")
    if card["candidates"]:
        lines.append(
            "Не подтверждено, рядом без связи: "
            + ", ".join(f"{k} ({v})" for k, v in group_counts(card["candidates"]).items())
        )
    if card["revisions"] and len(card["revisions"]) > 1:
        lines.append(
            "История: "
            + "; ".join(
                f"v{rev} {REVISION_ACTIONS.get(action, action)}"
                for rev, action in card["revisions"]
            )
        )
    lines.append("Источники:")
    for source in card["sources"]:
        role = ROLE_TEXT.get(source.get("author_role") or "", "")
        lines.append(
            f"  • №{source['telegram_message_id']} {source['message_date']:%d.%m %H:%M}"
            + (f" ({role})" if role else "")
            + f": {_clip(source['text'], 260)}"
            + (f"\n    {source['link']}" if source["link"] else "")
        )
    return "\n".join(lines)


CATEGORY_TEXT = {
    "homework": "возможное ДЗ",
    "check": "возможная проверочная",
    "event": "событие",
    "organizational": "организационное",
    "information": "информация",
    "other": "прочее",
}


def render_held_candidates(held, limit=12):
    """Owner-only footer: what the AI found but deliberately did not turn into a card."""
    lines = [f"Не карточки, сохранены для проверки ({len(held)}), студентам не показываются:"]
    for item in held[:limit]:
        label = item["title"] or _clip(item["description"] or "", 80)
        lines.append(
            f"  • {CATEGORY_TEXT.get(item['category'], item['category'])}: {label}"
            + f" (№{', №'.join(str(i) for i in item['cited'][:3])})"
            + (
                " — источник не подтверждён моделью"
                if item.get("source_status", "verified") != "verified"
                else ""
            )
        )
    if len(held) > limit:
        lines.append(f"  … и ещё {len(held) - limit}")
    return "\n".join(lines)


def render_preview(preview, chat_id, offset, size):
    """Chunks for one page of cards plus the owner-only footer for possible changes."""
    page = preview["cards"][offset : offset + size]
    blocks = [
        render_preview_card(offset + i, card, chat_id)[:MAX_CHUNK] for i, card in enumerate(page, 1)
    ]
    if offset + size >= len(preview["cards"]) and preview["review_marks"]:
        ids = sorted({mark["target_message_id"] for mark in preview["review_marks"]})
        blocks.append(
            "Отклонено валидацией, возможно изменение или отмена (проверить вручную): "
            + ", ".join(f"№{i}" for i in ids[:30])
        )
    held = preview.get("held_candidates") or []
    if offset + size >= len(preview["cards"]) and held:
        blocks.append(render_held_candidates(held))
    chunks, current = [], ""
    for block in blocks:
        if current and len(current) + len(block) + 2 > MAX_CHUNK:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks
