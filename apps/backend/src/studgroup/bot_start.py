"""Private /start walkthrough: one panel message, buttons only, invitation codes by link.

Reached from ``bot_panels.dispatch`` in mode ``start`` (callback prefix ``t``). The panel is the
same durable mechanism /st and /admin use: every new ``/start`` removes the previous screen and
the user's own messages, buttons carry a per-screen nonce and go stale when the screen changes.
Nothing here decides authorization: joining a group is ``headman_domain.redeem_invite``.

Student path: welcome -> "Я студент" -> join -> paste a code (or tap an invitation link) -> done.
Headman path: «Я староста» leaves a claim that ``bot_connect`` fulfils when the bot is added to a
new group. The promo-code step from the flow diagram is not built: Module 0 describes partner
codes but no partner model exists to record them.
"""

import json
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from studgroup import bot_info, bot_panels, subgroups
from studgroup import headman_domain as domain
from studgroup.bot_admin import enqueue, say
from studgroup.homework import utc
from studgroup.models import Group, HeadmanSession, Membership, StartSession, User

SESSION_MINUTES = 30
CLAIM_HOURS = 24  # how long «Я староста» waits for the bot to be added to a group
INFO_TOPICS = {
    "info_howto": "howto",
    "info_tariffs": "tariffs",
    "info_connect": "connect",
    "info_faq": "faq",
    "info_support": "support",
}
# Where a topic leads next, as buttons instead of «/command» links inside the text.
TOPIC_LINKS = {
    "faq": [("Сколько стоит", "info_tariffs"), ("Как подключиться", "info_connect")],
    "tariffs": [("Как подключиться", "info_connect")],
    "connect": [("Частые вопросы", "info_faq")],
}
_LINK_CODE = re.compile(r"invite_([A-Za-z0-9_-]{20,64})")
_BARE_CODE = re.compile(r"[A-Za-z0-9_-]{20,64}")


def invite_link(settings, code):
    """A tap-to-join link when the bot username is configured, otherwise the command text."""
    name = (settings.bot_username or "").lstrip("@")
    return f"https://t.me/{name}?start=invite_{code}" if name else f"/start invite_{code}"


def extract_code(text):
    """The invitation code from a pasted link, ``/start invite_...`` or the bare code."""
    text = (text or "").strip()
    found = _LINK_CODE.search(text)
    if found:
        return found.group(1)
    return text if _BARE_CODE.fullmatch(text) else None


def app_button(settings, label="Открыть StudGroup"):
    return {"text": label, "web_app": {"url": settings.webapp_origin}}


async def get_session(db, chat):
    return await db.scalar(
        select(StartSession).where(StartSession.telegram_user_id == chat).with_for_update()
    )


async def awaiting_input(db, chat):
    """True while the person is expected to type an invitation code."""
    session = await db.get(StartSession, chat)
    return bool(session and session.step == "code" and utc(session.expires_at) > datetime.now(UTC))


async def screen(db, session, text, rows, step, data=None, minutes=SESSION_MINUTES):
    session.nonce = secrets.token_hex(6)
    session.step = step
    session.payload = json.dumps(data or {})
    session.expires_at = datetime.now(UTC) + timedelta(minutes=minutes)
    keyboard = {
        "inline_keyboard": [
            [
                button
                if isinstance(button, dict)
                else {"text": button[0], "callback_data": f"t:{session.nonce}:{button[1]}"}
                for button in row
            ]
            for row in rows
        ]
    }
    await say(db, session.telegram_user_id, text[:4000], keyboard)


def info_row():
    return [("Как это работает", "info_howto"), ("Тарифы", "info_tariffs")], [
        ("Частые вопросы", "info_faq"),
        ("Поддержка", "info_support"),
    ]


async def topic_screen(db, session, settings, topic):
    """An info topic on the one /start panel, with its follow-ups as buttons."""
    rows = []
    if topic == "howto":
        rows.append([app_button(settings)])
    links = [(label, action) for label, action in TOPIC_LINKS.get(topic, [])]
    rows += [[link] for link in links]
    if topic != "support":
        rows.append([("Поддержка", "info_support")])
    rows.append([("‹ Назад", "home")])
    body = bot_info.render(topic, settings.support_contact)
    await screen(db, session, body, rows, "info")


async def current_membership(db, telegram_id):
    row = (
        await db.execute(
            select(Membership, Group)
            .join(User, User.id == Membership.user_id)
            .join(Group, Group.id == Membership.group_id)
            .where(
                User.telegram_user_id == telegram_id,
                Membership.status == "active",
                Group.status == "active",
                Group.pilot_authorized.is_(True),
            )
            .order_by(Group.name)
            .limit(1)
        )
    ).first()
    return row


async def home(db, session, settings, telegram_id):
    found = await current_membership(db, telegram_id)
    if found:
        member, group = found
        text = f"Вы подключены к «{group.name}»."
        rows = [[app_button(settings)]]
        if member.role in {"headman", "deputy"}:
            rows.insert(0, [("Кабинет старосты", "cabinet")])
        if member.role == "headman":
            rows.insert(1, [("Создать приглашение", "invite")])
        if member.role == "student" and await subgroups.splits(db, group.id):
            rows.insert(0, [("Мои подгруппы", "subgroups")])
        await screen(db, session, text, [*rows, *info_row()], "home")
        return
    await screen(
        db,
        session,
        "Привет! StudGroup собирает задания, дедлайны и расписание из сообщений вашей учебной "
        "группы и показывает их в приложении прямо в Telegram.\n\nКто вы?",
        [[("Я студент", "student"), ("Я староста", "headman")], *info_row()],
        "home",
    )


async def headman_prompt(db, session, settings, notice=None, groups=()):
    """«Я староста»: how to connect a group, or a pick among headman-less groups the person runs."""
    name = (settings.bot_username or "").lstrip("@")
    text = (
        "Подключение группы\n\n"
        "Добавьте бота в чат вашей группы: тот, кто добавил бота, сразу становится её "
        "старостой. Для этого нужно быть администратором чата. Бот в группе ничего не пишет.\n\n"
        "Сделайте бота администратором группы, иначе он не увидит сообщения чата.\n\n"
        "Старостой можно стать только в группе, где староста ещё не выбран."
    )
    if groups:
        text = (
            "Бот уже подключён к группам, где староста не выбран и где вы администратор. "
            "Выберите свою или добавьте бота в новую.\n\n" + text
        )
    if name:
        text += f"\nЕсли ничего не происходит, отправьте в группе /start@{name}."
    if notice:
        text = f"{notice}\n\n{text}"
    rows = []
    if name:
        rows.append(
            [{"text": "Добавить бота в группу", "url": f"https://t.me/{name}?startgroup=connect"}]
        )
    rows[:0] = [[(f"Стать старостой: {g.name[:40]}", f"pick{n}")] for n, g in enumerate(groups)]
    rows.append([("‹ Назад", "home")])
    await screen(
        db,
        session,
        text,
        rows,
        "headman",
        {"groups": [str(g.id) for g in groups]},
        minutes=CLAIM_HOURS * 60,
    )


async def headman_entry(db, session, settings, telegram_id):
    from studgroup import bot_connect  # late: bot_connect imports this module

    if await current_membership(db, telegram_id):
        await home(db, session, settings, telegram_id)
        return
    groups = await bot_connect.candidates(db, settings, telegram_id)
    await headman_prompt(db, session, settings, groups=groups)


async def connected(db, session, settings, group, bot_is_admin=True):
    note = (
        ""
        if bot_is_admin
        else "Сделайте бота администратором группы: иначе он не увидит сообщения чата.\n\n"
    )
    await screen(
        db,
        session,
        f"Готово! Группа «{group.name}» подключена, вы её староста.\n\n"
        f"{note}"
        "Часовой пояс группы по умолчанию: Москва. Изменить его можно в кабинете.\n\n"
        "Теперь пригласите студентов.",
        [
            [("Создать приглашение", "invite")],
            [("Кабинет старосты", "cabinet")],
            [app_button(settings)],
        ],
        "connected",
    )


async def invite(db, session, settings, telegram_id):
    found = await current_membership(db, telegram_id)
    if not found or found[0].role != "headman":
        await home(db, session, settings, telegram_id)
        return
    member, group = found
    try:
        code = (await domain.issue_invites(db, group, member, 1))[0]
    except domain.StError as error:
        await screen(db, session, str(error), [[("‹ Назад", "home")]], "invite")
        return
    await screen(
        db,
        session,
        "Приглашение для студента\n\n"
        f"{invite_link(settings, code)}\n\n"
        "Перешлите это одному студенту: достаточно нажать на ссылку. Она действует 24 часа и "
        "срабатывает один раз.",
        [[("Создать ещё", "invite")], [("‹ Назад", "home")]],
        "invite",
    )


async def switch_panel(db, chat, leaving, entering):
    """Hand the chat from one panel to another: the old screen disappears, the new one is sent."""
    for mode in (leaving, entering):
        await bot_panels.reset(db, await bot_panels.panel(db, chat, mode))
    await bot_panels.activate(db, chat, entering)


async def open_cabinet(db, telegram_id):
    """«Кабинет старосты»: the same cabinet /st opens, reached by a button and one tap shorter."""
    from studgroup import headman_bot  # late: headman_bot imports this module

    if not await domain.memberships(db, telegram_id):
        return False
    await switch_panel(db, telegram_id, "start", "st")
    session = await db.scalar(
        select(HeadmanSession)
        .where(HeadmanSession.telegram_user_id == telegram_id)
        .with_for_update()
    )
    if session is None:
        session = HeadmanSession(
            telegram_user_id=telegram_id,
            nonce="",
            step="groups",
            payload="{}",
            expires_at=datetime.now(UTC),
        )
        db.add(session)
    token = bot_panels.scope.set((telegram_id, "st"))
    try:
        await headman_bot.enter(db, session)
    finally:
        bot_panels.scope.reset(token)
    return True


async def open_start(db, settings, telegram_id):
    """Back from the cabinet to the start screen."""
    await switch_panel(db, telegram_id, "st", "start")
    session = await get_session(db, telegram_id)
    if session is None:
        session = StartSession(
            telegram_user_id=telegram_id,
            nonce="",
            step="home",
            payload="{}",
            expires_at=datetime.now(UTC),
        )
        db.add(session)
    token = bot_panels.scope.set((telegram_id, "start"))
    try:
        await home(db, session, settings, telegram_id)
    finally:
        bot_panels.scope.reset(token)


async def join(db, session, notice=None):
    text = (
        "Подключение к группе\n\n"
        "Нужен одноразовый код приглашения от вашего старосты, он действует 24 часа. "
        "Если староста прислал ссылку, достаточно нажать на неё: вводить код не нужно."
    )
    if notice:
        text = f"{notice}\n\n{text}"
    await screen(db, session, text, [[("Ввести код", "enter_code")], [("‹ Назад", "home")]], "join")


async def code_prompt(db, session, notice=None):
    text = "Отправьте код приглашения одним сообщением. Я сразу удалю его из чата."
    if notice:
        text = f"{notice}\n\n{text.replace('Отправьте', 'Отправьте ещё раз')}"
    await screen(db, session, text, [[("‹ Назад", "student")]], "code")


async def redeem(db, session, settings, telegram_id, code, *, typed):
    """Join by code. A typed code that fails keeps the person waiting for another try."""
    try:
        if not code:
            raise domain.StError("Это не похоже на код приглашения. Скопируйте его целиком.")
        async with db.begin_nested():
            group = await domain.redeem_invite(db, settings, telegram_id, code)
    except domain.StError as error:
        if typed:
            await code_prompt(db, session, f"Не получилось: {error}")
        else:
            await join(db, session, f"Не получилось: {error}")
        return
    found = await current_membership(db, telegram_id)
    member = found[0] if found and found[1].id == group.id else None
    if member and await subgroups.unanswered(db, member):
        await subgroup_choice(db, session, settings, member, group, joined=True)
    else:
        await screen(
            db,
            session,
            f"Готово! Вы подключены к «{group.name}».",
            [[app_button(settings)]],
            "done",
        )


async def subgroup_choice(db, session, settings, member, group, *, joined=False):
    """Show the next unanswered split, or the member's complete editable list."""
    all_splits = await subgroups.splits(db, group.id)
    picked = await subgroups.choices(db, member.id)
    unanswered = [pair for pair in all_splits if pair[0].id not in picked]
    if unanswered:
        split, choices = unanswered[0]
        prefix = f"Готово! Вы подключены к «{group.name}».\n\n" if joined else ""
        await screen(
            db,
            session,
            prefix + f"К какой подгруппе по предмету «{split.subject_name}» вы относитесь?\n\n"
            "Выбор можно изменить позже.",
            [[(row.name, f"subgroup_pick_{n}")] for n, row in enumerate(choices)]
            + [[("‹ Назад", "home")]],
            "subgroups",
            {
                "member_id": str(member.id),
                "split_id": str(split.id),
                "subgroups": [str(row.id) for row in choices],
            },
        )
        return
    lines, rows = [f"Ваши подгруппы в «{group.name}»:"], []
    for n, (split, choices) in enumerate(all_splits):
        chosen = next((row for row in choices if row.id == picked.get(split.id)), None)
        lines.append(f"• {split.subject_name}: {chosen.name if chosen else 'не выбрана'}")
        rows.append([(f"Изменить · {split.subject_name}"[:55], f"subgroup_change_{n}")])
    rows += [[app_button(settings)], [("‹ В начало", "home")]]
    await screen(
        db,
        session,
        "\n".join(lines) if all_splits else "В вашей группе пока нет предметов с подгруппами.",
        rows,
        "subgroups_done",
        {"member_id": str(member.id), "splits": [str(pair[0].id) for pair in all_splits]},
    )


async def forget(db, chat, message):
    """Remove the person's own message so an invitation code does not stay in the chat."""
    owner = await bot_panels.panel(db, chat, "start")
    bot_panels.job(
        db,
        "deleteMessage",
        {"chat_id": chat, "message_id": message.message_id, "_outgoing": False},
        owner,
    )


async def handle(db, update, settings, topic=None):
    callback = update.callback_query
    message = callback.message if callback else update.message
    sender = callback.sender if callback else message.sender if message else None
    if not message or not sender or message.chat.type != "private" or message.chat.id != sender.id:
        return False
    chat = sender.id
    if callback:
        enqueue(db, "answerCallbackQuery", {"callback_query_id": callback.id})
    session = await get_session(db, chat)
    if session is None:
        session = StartSession(
            telegram_user_id=chat, nonce="", step="home", payload="{}", expires_at=datetime.now(UTC)
        )
        db.add(session)
    text = (message.text or "").strip()
    if topic:  # an info command or a landing-page deep link: shown on the panel like a button
        await topic_screen(db, session, settings, topic)
        return True
    if not callback:
        parts = text.split(maxsplit=1)
        if parts and parts[0].split("@")[0] == "/start":
            argument = parts[1].strip() if len(parts) == 2 else ""
            if argument == "headman":  # the button under the bot's note in the group chat
                await headman_entry(db, session, settings, sender.id)
            elif argument.startswith("invite_"):
                await forget(db, chat, message)  # the code is a secret: never leave it in the chat
                await redeem(db, session, settings, sender.id, extract_code(argument), typed=False)
            elif argument == "subgroups":
                found = await current_membership(db, sender.id)
                if found:
                    await subgroup_choice(db, session, settings, found[0], found[1])
                else:
                    await home(db, session, settings, sender.id)
            else:
                await home(db, session, settings, sender.id)
            return True
        if text in {"/cancel", "Отмена"}:
            await home(db, session, settings, sender.id)
            return True
        if session.step == "code" and utc(session.expires_at) > datetime.now(UTC):
            await forget(db, chat, message)
            await redeem(db, session, settings, sender.id, extract_code(text), typed=True)
            return True
        return False
    action = (callback.data or "").split(":", 2)[2]
    if action == "home":
        await home(db, session, settings, sender.id)
    elif action == "student":
        await join(db, session)
    elif action == "enter_code":
        await code_prompt(db, session)
    elif action == "headman":
        await headman_entry(db, session, settings, sender.id)
    elif action.startswith("pick") and session.step == "headman":
        from studgroup import bot_connect

        try:
            group = await db.get(
                Group, uuid.UUID(json.loads(session.payload)["groups"][int(action[4:])])
            )
        except (KeyError, IndexError, ValueError):
            group = None
        if group is None:
            await headman_entry(db, session, settings, sender.id)
        else:
            await bot_connect.claim_headman(db, settings, session, group, sender.id)
    elif action == "invite":
        await invite(db, session, settings, sender.id)
    elif action == "cabinet":
        if not await open_cabinet(db, sender.id):
            await home(db, session, settings, sender.id)
    elif action == "subgroups":
        found = await current_membership(db, sender.id)
        if found:
            await subgroup_choice(db, session, settings, found[0], found[1])
        else:
            await home(db, session, settings, sender.id)
    elif session.step == "subgroups" and action.startswith("subgroup_pick_"):
        data = json.loads(session.payload)
        found = await current_membership(db, sender.id)
        if not found or str(found[0].id) != data.get("member_id"):
            await home(db, session, settings, sender.id)
        else:
            index = int(action.removeprefix("subgroup_pick_"))
            await subgroups.choose(
                db,
                found[0],
                uuid.UUID(data["split_id"]),
                uuid.UUID(data["subgroups"][index]),
            )
            await subgroup_choice(db, session, settings, found[0], found[1])
    elif session.step == "subgroups_done" and action.startswith("subgroup_change_"):
        data = json.loads(session.payload)
        found = await current_membership(db, sender.id)
        if not found or str(found[0].id) != data.get("member_id"):
            await home(db, session, settings, sender.id)
        else:
            split_id = uuid.UUID(data["splits"][int(action.removeprefix("subgroup_change_"))])
            split, choices = next(
                pair for pair in await subgroups.splits(db, found[1].id) if pair[0].id == split_id
            )
            await screen(
                db,
                session,
                f"Выберите подгруппу по предмету «{split.subject_name}».",
                [[(row.name, f"subgroup_pick_{n}")] for n, row in enumerate(choices)]
                + [[("‹ Назад", "subgroups")]],
                "subgroups",
                {
                    "member_id": str(found[0].id),
                    "split_id": str(split.id),
                    "subgroups": [str(row.id) for row in choices],
                },
            )
    elif action in INFO_TOPICS:
        await topic_screen(db, session, settings, INFO_TOPICS[action])
    else:
        await home(db, session, settings, sender.id)
    return True
