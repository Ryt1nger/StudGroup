"""Public info topics: tariffs, how-to, connect, FAQ, support.

Reachable from buttons of the /start panel, by the old commands (/tariffs, /help, /connect, /faq,
/support) and by Telegram deep links ``https://t.me/<bot>?start=<topic>`` used by the landing page;
the last two only select the topic, ``bot_start`` shows it inside the single /start panel so no
loose messages pile up. Read-only: no state, no authorization decisions. Deliberately contains no
prices: tariffs are not published yet. Texts never tell people to type a command.
"""

COMMANDS = {
    "/tariffs": "tariffs",
    "/help": "howto",
    "/howto": "howto",
    "/connect": "connect",
    "/faq": "faq",
    "/support": "support",
}

# Deep-link payloads (Telegram allows A-Z a-z 0-9 _ -). ``invite_*`` is NOT here: it belongs
# to the headman invitation flow and must keep falling through to it.
PAYLOADS = {
    "tariffs": "tariffs",
    "howto": "howto",
    "help": "howto",
    "connect": "connect",
    "faq": "faq",
    "support": "support",
}


def topic_for(text):
    """Return the topic for a /start payload or an info command, otherwise None."""
    parts = (text or "").split()
    if not parts:
        return None
    head = parts[0].split("@")[0].lower()
    if head == "/start":
        return PAYLOADS.get(parts[1].lower()) if len(parts) == 2 else None
    return COMMANDS.get(head)


def render(topic, support_contact=""):
    contact = (support_contact or "").strip()
    if topic == "tariffs":
        body = (
            "Тарифы\n\n"
            "Тарифы StudGroup скоро появятся. Сейчас приложение работает в закрытом пилоте, "
            "цены пока не опубликованы.\n"
            "Актуальная информация будет здесь и на сайте."
        )
    elif topic == "howto":
        body = (
            "Как пользоваться StudGroup\n\n"
            "StudGroup собирает из сообщений вашей учебной группы задания, дедлайны, изменения "
            "в расписании и важные объявления и показывает их в приложении прямо в Telegram.\n\n"
            "• «Сегодня» — ближайшая пара и то, что важно сегодня и завтра\n"
            "• «Задания» — домашние задания и контрольные\n"
            "• «Расписание» — пары на неделю\n\n"
            "У карточки есть ссылка на исходное сообщение в чате группы. "
            "Отметка «выполнено» ставится лично для вас.\n\n"
            "Приложение доступно участникам подключённой группы. Если вы ещё не подключены, "
            "нажмите на ссылку-приглашение от старосты"
        )
    elif topic == "connect":
        body = (
            "Подключение группы\n\n"
            "Студент: возьмите у старосты ссылку-приглашение (она действует 24 часа и "
            "срабатывает один раз) и нажмите на неё. Подключиться можно только к одной группе.\n\n"
            "Староста: на стартовом экране бота нажмите «Я староста» и добавьте бота "
            "администратором в чат группы. Группа подключится сразу."
        )
        if contact:
            body += f"\n\nВопросы: {contact}"
    elif topic == "faq":
        body = (
            "Частые вопросы\n\n"
            "Какие данные анализируются?\n"
            "Сообщения учебной группы, к которой подключён бот: из них собираются задания, "
            "дедлайны, расписание и объявления.\n\n"
            "Кто видит карточки?\n"
            "Участники подключённой группы.\n\n"
            "Можно ли попробовать бесплатно?\n"
            "Сейчас идёт закрытый пилот, условия пробного доступа уточняются.\n\n"
            "Остальное — по кнопкам ниже."
        )
    else:  # support
        body = (
            f"Поддержка\n\nНапишите нам: {contact}"
            if contact
            else "Поддержка\n\nКонтакты поддержки скоро появятся здесь."
        )
    return body
