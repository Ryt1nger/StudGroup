"""Owner diagnostic: replay one message through the live AI path and show what the model saw."""

from datetime import UTC, datetime

from sqlalchemy import select

from studgroup.ai import DeepSeekProvider, ProviderFailure
from studgroup.models import RawMessage


class RecordingProvider(DeepSeekProvider):
    """Keeps the raw model answers so the owner can see exactly what was decided."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.answers = []

    async def _complete(self, payload):
        body = await super()._complete(payload)
        try:
            self.answers.append(body["choices"][0]["message"]["content"].strip())
        except (KeyError, IndexError, TypeError):
            self.answers.append("<нет ответа>")
        return body


async def explain(db, settings, group, telegram_message_id):
    from studgroup.processing import context_for

    raw = await db.scalar(
        select(RawMessage).where(
            RawMessage.group_id == group.id,
            RawMessage.telegram_message_id == telegram_message_id,
        )
    )
    if raw is None:
        return f"Сообщение {telegram_message_id} не найдено в группе «{group.name}»."
    now = datetime.now(UTC)
    context, schedule = await context_for(db, raw, group, now, semantic_neighborhood=True)
    lines = [f"Сообщение {telegram_message_id}: {(raw.text or '')[:200]}", ""]
    lines.append(f"ИИ получил {len(context)} сообщ. (цель отмечена *):")
    for item in context:
        mark = "*" if item["is_target"] else " "
        lines.append(f"{mark}{item['message_id']}: {(item['text'] or '')[:70]}")
    structured = any(item.get("group_structure") for item in context)
    lines.append(f"Карта группы передана: {'да' if structured else 'нет'}")
    provider = RecordingProvider(
        settings.deepseek_api_key.get_secret_value(),
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
    )
    try:
        await provider.screen_batch(context, group.timezone, telegram_message_id)
        lines.append(f"Лёгкий проход ответил: {provider.answers[-1][:300]}")
        await provider.extract_batch(
            context, group.timezone, schedule, target_message_id=telegram_message_id
        )
        lines.append(f"Глубокий проход ответил: {provider.answers[-1][:900]}")
    except ProviderFailure as error:
        lines.append(f"Ошибка ИИ: {error.args[0] if error.args else error.__class__.__name__}")
    return "\n".join(lines)[:3900]
