"""Public read-only information topics in StudGroup's single /start bot panel.

Published group prices are informational; payment collection is feature-gated,
and Telegram-native digital access uses Stars rather than bank transfer links.
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
    "about": "about",
    "partners": "partners",
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
    if topic == "about":
        body = (
            "О нас\n\n"
            "StudGroup помогает учебным группам не терять важное в Telegram. "
            "Сервис собирает задания, сроки, расписание и события в приложении, "
            "которое открывается прямо из Telegram.\n\n"
            "Староста подключает чат, а студенты получают доступ по приглашению "
            "после проверки участия в группе."
        )
    elif topic == "partners":
        body = (
            "Партнёрка\n\n"
            "Партнёрская программа StudGroup готовится. "
            "Условия участия и вознаграждения пока не опубликованы. "
            "Когда программу запустят, информация появится здесь."
        )
    elif topic == "tariffs":
        body = (
            "Тарифы StudGroup\n\n"
            "🎁 Первые 7 дней для всей учебной группы бесплатно.\n"
            "После — 800 ₽ за 30 дней для всей группы, не за одного студента.\n"
            "Один платёж можно внести за 1, 2, 3 или 4 месяца: "
            "800 / 1 600 / 2 400 / 3 200 ₽.\n\n"
            "В группе из 30–35 человек это всего около 23–27 ₽ с каждого; "
            "при 25–30 — 27–32 ₽, а при 20 — 40 ₽. "
            "Собирать взносы со студентов StudGroup не требует.\n\n"
            "Внутри Telegram цифровой доступ оплачивается звёздами Telegram Stars. "
            "Подключение оплаты зависит от готовности платёжной системы."
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
            "Студент: возьмите у старосты групповую ссылку-приглашение и нажмите на неё. "
            "Бот проверит, что вы состоите в учебном Telegram-чате. Подключиться можно "
            "только к одной группе. Ранее выданные одноразовые приглашения тоже работают "
            "до истечения своего срока.\n\n"
            "Староста: нажмите «Подключить группу» → «Я староста», добавьте бота "
            "администратором существующего чата, проверьте права, подтвердите группу "
            "и укажите официальный идентификатор РАНХиГС перед отправкой приглашений."
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
