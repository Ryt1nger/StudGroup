"""Self-service group connection (owner decisions, 21-22 Oct 2026).

Adding the bot to a Telegram group is all it takes: the bot creates the group and its working
environment itself and starts at once, with no approval, and **the person who added the bot becomes
the group's single headman automatically**. The bot never writes in the group.

Who counts as the person who added the bot depends on the trigger:

* the bot's own membership update and the service message "X invited StudGroup" name the adder
  directly; they become the headman unless Telegram positively says they are only an ordinary
  member (when Telegram cannot say, the adder is trusted);
* a ``/start`` typed in the group is only a fallback for when those updates are not delivered; the
  person who typed it must be positively confirmed as an administrator of the chat.

If nobody could be made the headman (the adder is an ordinary member, or already belongs to another
group) the group simply waits without a headman; an administrator of that chat can take the role
later with «Я староста» in the private /start screen. A group that already has a headman is never
changed, and the role is assigned under a lock on the group row, so two people cannot both win.
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
            timezone=DEFAULT_TIMEZONE,
            status="active",
            pilot_authorized=True,
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
    """The bot is in this chat: make sure the group exists and the adder is its headman."""
    group, _ = await ensure_group(db, chat_id, title)
    if await has_headman(db, group.id):
        return
    admin = await is_chat_admin(settings, chat_id, sender_id)
    if admin is False or (typed and admin is not True):
        return  # not provably the one who may run this group: wait for «Я староста»
    own = await bot_start.current_membership(db, sender_id)
    if own and own[1].id != group.id:
        return  # one person, one group
    headed = await make_headman(db, settings, group, sender_id)
    if headed is not None:
        await tell_new_headman(db, settings, headed, sender_id)


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
    if await make_headman(db, settings, locked, telegram_id) is None:
        await refuse("Не удалось назначить старосту: у группы уже есть староста.")
        return False
    bot_id = own_bot_id(settings)
    bot_is_admin = (
        bot_id is not None
        and await chat_status(settings, locked.telegram_chat_id, bot_id) == "administrator"
    )
    await bot_start.connected(db, session, settings, locked, bot_is_admin=bot_is_admin)
    return True
