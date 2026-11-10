"""Group discovery and explicitly confirmed ownership through private Telegram buttons.

Adding the bot can automatically advance a waiting administrator to the private confirmation
screen; it never grants the headman role before an explicit button confirmation.
The bot stays silent in source group chats.
"""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from studgroup import bot_admin, bot_panels, bot_start
from studgroup.group_structure import ensure_profile
from studgroup.models import Group, Membership, StartSession, User

GROUP_TYPES = {"group", "supergroup"}
DEFAULT_TIMEZONE = "Europe/Moscow"
ADMIN_STATUSES = {"creator", "administrator"}
CANDIDATE_LIMIT = 10
NO_HEADMAN = "староста не выбран"


def own_bot_id(settings):
    try:
        return int(settings.telegram_bot_token.split(":", 1)[0])
    except (TypeError, ValueError):
        return None


async def chat_status(settings, chat_id, user_id):
    """The person's Telegram status in the chat (creator, administrator, member, ...) or None."""
    try:
        data = await bot_admin.telegram_request(
            settings, "getChatMember", {"chat_id": chat_id, "user_id": user_id}
        )
    except (httpx.HTTPError, ValueError):
        return None
    result = data.get("result") if data.get("ok") else None
    return result.get("status") if isinstance(result, dict) else None


async def has_headman(db, group_id):
    return bool(
        await db.scalar(
            select(Membership.id).where(
                Membership.group_id == group_id,
                Membership.role == "headman",
                Membership.status == "active",
            )
        )
    )


async def headmanless_group_ids(db):
    """Ids of active groups with no active headman (for the owner list and the claim picker)."""
    headed = select(Membership.group_id).where(
        Membership.role == "headman", Membership.status == "active"
    )
    rows = await db.scalars(
        select(Group.id).where(Group.status == "active", Group.id.not_in(headed))
    )
    return set(rows)


async def is_chat_admin(settings, chat_id, user_id):
    """True/False when Telegram says whether the person administers the chat, None if it cannot.

    The administrator list can be read by any bot that is a member of the chat, so this works even
    before the bot has been made an administrator.
    """
    try:
        data = await bot_admin.telegram_request(
            settings, "getChatAdministrators", {"chat_id": chat_id}
        )
    except (httpx.HTTPError, ValueError):
        data = None
    result = data.get("result") if data and data.get("ok") else None
    if isinstance(result, list):
        return any((entry.get("user") or {}).get("id") == user_id for entry in result)
    status = await chat_status(settings, chat_id, user_id)
    return None if status is None else status in ADMIN_STATUSES


async def candidates(db, settings, telegram_id):
    """Headman-less groups the person administers in Telegram; names of others are never shown."""
    ids = await headmanless_group_ids(db)
    if not ids:
        return []
    groups = (
        await db.scalars(
            select(Group).where(Group.id.in_(ids)).order_by(Group.name).limit(CANDIDATE_LIMIT)
        )
    ).all()
    return [
        group
        for group in groups
        if await chat_status(settings, group.telegram_chat_id, telegram_id) in ADMIN_STATUSES
    ]


async def on_my_chat_member(db, settings, update):
    bot_id = own_bot_id(settings)
    if (
        update.chat.type in GROUP_TYPES
        and bot_id is not None
        and update.new_chat_member.user.id == bot_id
        and update.new_chat_member.status in {"member", "administrator"}
    ):
        await connect(db, settings, update.chat.id, update.chat.title, update.sender.id)


async def on_group_message(db, settings, message):
    """The bot being added or a /start command in a group starts (or finishes) a connection."""
    if message.chat.type not in GROUP_TYPES or message.sender is None:
        return
    bot_id = own_bot_id(settings)
    added = bot_id is not None and any(m.id == bot_id for m in message.new_chat_members)
    command = (message.text or "").lstrip().startswith("/start")
    if added or command:
        await connect(
            db,
            settings,
            message.chat.id,
            message.chat.title,
            message.sender.id,
            typed=command and not added,
        )


async def ensure_group(db, chat_id, title):
    """The group for this chat, created with its environment when it is new: (group, created)."""
    now = datetime.now(UTC)
    created = await db.execute(
        insert(Group)
        .values(
            id=uuid.uuid4(),
            telegram_chat_id=chat_id,
            name=(title or "").strip()[:255] or "Группа",
            academic_group_code_required=True,
            timezone=DEFAULT_TIMEZONE,
            status="active",
            # Discovery is not approval: no raw-message ingestion or AI charges yet.
            pilot_authorized=False,
            bot_added_at=now,
        )
        .on_conflict_do_nothing(index_elements=[Group.telegram_chat_id])
    )
    group = await db.scalar(select(Group).where(Group.telegram_chat_id == chat_id))
    if created.rowcount == 1:
        # The bot sets the whole working environment up now rather than on the first message.
        await ensure_profile(db, group.id, now)
    return group, created.rowcount == 1


async def connect(db, settings, chat_id, title, sender_id, *, typed=False):
    """Discover the chat and advance a waiting administrator without claiming the role."""
    group, _created = await ensure_group(db, chat_id, title)
    if group.status != "active" or await has_headman(db, group.id):
        return

    # The user must have explicitly started onboarding in the private bot chat.
    # Never send unsolicited messages to the person who added/promoted the bot.
    session = await bot_start.get_session(db, sender_id)
    if (
        session is None
        or session.step != "headman"
        or bot_start.utc(session.expires_at) <= datetime.now(UTC)
        or await bot_start.current_membership(db, sender_id)
    ):
        return

    bot_id = own_bot_id(settings)
    if (
        bot_id is None
        or await chat_status(settings, chat_id, bot_id) != "administrator"
        or await is_chat_admin(settings, chat_id, sender_id) is not True
    ):
        return

    # The group was discovered by a Telegram update, but the next step is still
    # an explicit confirmation. Group activation and membership happen later.
    token = bot_panels.scope.set((sender_id, "start"))
    try:
        await bot_start.confirm_group_screen(db, session, group)
    finally:
        bot_panels.scope.reset(token)


async def make_headman(db, settings, group, telegram_id):
    """Give the role under a lock and tell the new headman in private (if they ever opened the bot)."""
    locked = await db.scalar(
        select(Group)
        .where(Group.id == group.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.status != "active" or await has_headman(db, locked.id):
        return None
    await db.execute(
        insert(User)
        .values(id=uuid.uuid4(), telegram_user_id=telegram_id, display_name="Староста")
        .on_conflict_do_nothing(index_elements=[User.telegram_user_id])
    )
    user = await db.scalar(select(User).where(User.telegram_user_id == telegram_id))
    member = await db.scalar(
        select(Membership)
        .where(Membership.user_id == user.id, Membership.group_id == locked.id)
        .with_for_update()
    )
    if member is None:
        db.add(
            Membership(
                id=uuid.uuid4(),
                user_id=user.id,
                group_id=locked.id,
                role="headman",
                status="active",
            )
        )
    else:
        member.role, member.status = "headman", "active"
    await db.flush()
    return locked


async def tell_new_headman(db, settings, group, telegram_id):
    """The private «group connected» screen. Delivery fails quietly if the person never opened the bot."""
    session = await bot_start.get_session(db, telegram_id)
    if session is None:
        session = StartSession(
            telegram_user_id=telegram_id,
            nonce="",
            step="home",
            payload="{}",
            expires_at=datetime.now(UTC),
        )
        db.add(session)
        await db.flush()
    bot_id = own_bot_id(settings)
    bot_is_admin = (
        bot_id is not None
        and await chat_status(settings, group.telegram_chat_id, bot_id) == "administrator"
    )
    token = bot_panels.scope.set((telegram_id, "start"))
    try:
        await bot_start.connected(db, session, settings, group, bot_is_admin=bot_is_admin)
    finally:
        bot_panels.scope.reset(token)


async def claim_headman(db, settings, session, group, telegram_id):
    """«Я староста» picked a headman-less group: take the role if Telegram confirms the person."""

    async def refuse(notice):
        await bot_start.headman_prompt(db, session, settings, notice)

    locked = await db.scalar(
        select(Group)
        .where(Group.id == group.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None or locked.status != "active":
        await refuse("Эта группа сейчас недоступна.")
        return False
    if await has_headman(db, locked.id):
        await refuse("У этой группы уже есть староста. Роль нельзя занять второй раз.")
        return False
    own = await bot_start.current_membership(db, telegram_id)
    if own and own[1].id != locked.id:
        await refuse("Вы уже подключены к группе. Один человек может быть только в одной группе.")
        return False
    if await is_chat_admin(settings, locked.telegram_chat_id, telegram_id) is not True:
        await refuse("Стать старостой может только администратор этой группы в Telegram.")
        return False
    bot_id = own_bot_id(settings)
    if (
        bot_id is None
        or await chat_status(settings, locked.telegram_chat_id, bot_id) != "administrator"
    ):
        await refuse("Для подключения назначьте StudGroup администратором Telegram-группы.")
        return False
    from studgroup import legal

    user = await db.scalar(select(User).where(User.telegram_user_id == telegram_id))
    if settings.registration_legal_required and (
        not legal.legal_ready(settings)
        or user is None
        or not await legal.has_consent(db, user.id, "terms", settings)
        or not await legal.has_consent(db, user.id, "service", settings)
    ):
        await refuse("Сначала подтвердите действующие условия и отдельное согласие в боте.")
        return False
    if await make_headman(db, settings, locked, telegram_id) is None:
        await refuse("Не удалось назначить старосту: у группы уже есть староста.")
        return False
    # Activation is part of the same transaction as verified, explicit headman claiming.
    locked.pilot_authorized = True
    if settings.owner_funded_pilot:
        from studgroup.analysis.pilot_access import ensure_route

        await ensure_route(db, locked)
    bot_id = own_bot_id(settings)
    bot_is_admin = (
        bot_id is not None
        and await chat_status(settings, locked.telegram_chat_id, bot_id) == "administrator"
    )
    await bot_start.connected(db, session, settings, locked, bot_is_admin=bot_is_admin)
    return True
