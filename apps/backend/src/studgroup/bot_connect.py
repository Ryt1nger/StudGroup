"""Self-service group connection (owner decisions, 21 Oct 2026).

Adding the bot to a Telegram group is all it takes for StudGroup to set the group up: the bot
creates the group and its working environment itself and starts at once, with no approval. The
headman is a separate, later fact:

* if the person who added the bot had tapped «Я староста» in the private /start walkthrough (a
  claim), they become the group's single headman straight away;
* otherwise the group exists with «староста не выбран»; the bot says so once in the group, and
  any administrator of that chat can become the headman later by tapping «Я староста».

Safeguards: Telegram must confirm that the claimant is the group's creator or an administrator and
that the bot is an administrator; a group that already has a headman can never be claimed; a person
already in another group cannot claim this one; a headman is assigned under a lock on the group, so
two claimants cannot both win.

Triggers: the bot's own membership update, the service message about new members, or a ``/start``
command typed in the group (a fallback when membership updates are not delivered).
"""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from studgroup import bot_admin, bot_panels, bot_start
from studgroup.group_structure import ensure_profile
from studgroup.homework import utc
from studgroup.models import Group, Membership, User

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


async def claim_for(db, telegram_id):
    """The person's live headman claim, or None."""
    session = await bot_start.get_session(db, telegram_id)
    if session and session.step == "headman" and utc(session.expires_at) > datetime.now(UTC):
        return session
    return None


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
        await connect(db, settings, message.chat.id, message.chat.title, message.sender.id)


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


async def connect(db, settings, chat_id, title, sender_id):
    group, created = await ensure_group(db, chat_id, title)
    claim = await claim_for(db, sender_id)
    if claim is not None:
        token = bot_panels.scope.set((sender_id, "start"))
        try:
            await claim_headman(db, settings, claim, group, sender_id)
        finally:
            bot_panels.scope.reset(token)
    if created and not await has_headman(db, group.id):
        await announce_no_headman(db, settings, group)


async def announce_no_headman(db, settings, group):
    """One message in the group: it is connected, and it needs a headman."""
    name = (settings.bot_username or "").lstrip("@")
    payload = {
        "chat_id": group.telegram_chat_id,
        "text": (
            "StudGroup подключён к этой группе. Староста пока не выбран: пусть староста "
            "откроет бота и нажмёт «Я староста», это займёт минуту."
        ),
    }
    if name:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "Я староста", "url": f"https://t.me/{name}?start=headman"}]
            ]
        }
    await bot_admin.enqueue_once(db, "sendMessage", payload, f"group-hint:{group.telegram_chat_id}")


async def claim_headman(db, settings, session, group, telegram_id):
    """Make the person the group's headman if every safeguard holds; reports on their panel."""

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
    bot_id = own_bot_id(settings)
    if bot_id is None or await chat_status(settings, locked.telegram_chat_id, bot_id) != (
        "administrator"
    ):
        await refuse(
            "Бот добавлен в группу, но он не администратор. Сделайте бота администратором "
            "группы: я сразу напишу, когда увижу это."
        )
        return False
    if await chat_status(settings, locked.telegram_chat_id, telegram_id) not in ADMIN_STATUSES:
        await refuse("Стать старостой может только администратор этой группы в Telegram.")
        return False
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
    await bot_start.connected(db, session, settings, locked)
    return True
