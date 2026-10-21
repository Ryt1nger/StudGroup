"""Self-service group connection (owner decision, 21 Oct 2026).

A person who tapped «Я староста» in the private /start walkthrough holds a claim (the start session sits on step ``headman``). When the bot is then added to a Telegram group
that StudGroup has never seen, that person becomes its single headman and the group starts at once,
with no manual approval. Safeguards that keep the rule from being abused:

* the group must not exist in the database in any state, so nobody can take over a connected group;
* Telegram must confirm that the claimant is the group's creator or an administrator and that the
  bot itself is an administrator;
* a person already connected to a group cannot connect another (one group per person);
* the unique ``telegram_chat_id`` makes the creation race-safe: only one claimant can win.

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


async def claim_for(db, telegram_id):
    """The person's live headman claim, or None."""
    session = await bot_start.get_session(db, telegram_id)
    if session and session.step == "headman" and utc(session.expires_at) > datetime.now(UTC):
        return session
    return None


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
    """Unknown group: only the bot being added or a /start command can start a connection."""
    if message.chat.type not in GROUP_TYPES or message.sender is None:
        return
    bot_id = own_bot_id(settings)
    added = bot_id is not None and any(m.id == bot_id for m in message.new_chat_members)
    command = (message.text or "").lstrip().startswith("/start")
    if added or command:
        await connect(db, settings, message.chat.id, message.chat.title, message.sender.id)


async def connect(db, settings, chat_id, title, sender_id):
    claim = await claim_for(db, sender_id)
    if claim is None:
        return  # nobody asked to be this group's headman; the bot stays silent in the group
    token = bot_panels.scope.set((sender_id, "start"))
    try:
        await _connect(db, settings, claim, chat_id, title, sender_id)
    finally:
        bot_panels.scope.reset(token)


async def _connect(db, settings, claim, chat_id, title, sender_id):
    async def refuse(notice):
        await bot_start.headman_prompt(db, claim, settings, notice)

    if await db.scalar(select(Group.id).where(Group.telegram_chat_id == chat_id)):
        await refuse(
            "Эта группа уже есть в StudGroup. Старостой можно стать только при первом "
            "подключении группы."
        )
        return
    if await bot_start.current_membership(db, sender_id):
        await refuse("Вы уже подключены к группе. Один человек может быть только в одной группе.")
        return
    bot_id = own_bot_id(settings)
    if bot_id is None or await chat_status(settings, chat_id, bot_id) != "administrator":
        await refuse(
            "Бот добавлен в группу, но он не администратор. Сделайте бота администратором "
            "группы: я сразу напишу, когда увижу это."
        )
        return
    if await chat_status(settings, chat_id, sender_id) not in {"creator", "administrator"}:
        await refuse("Подключить группу может только её администратор в Telegram.")
        return
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
    if created.rowcount != 1:  # someone else connected it a moment ago
        await refuse("Эта группа уже есть в StudGroup.")
        return
    group = await db.scalar(select(Group).where(Group.telegram_chat_id == chat_id))
    await db.execute(
        insert(User)
        .values(id=uuid.uuid4(), telegram_user_id=sender_id, display_name="Староста")
        .on_conflict_do_nothing(index_elements=[User.telegram_user_id])
    )
    user = await db.scalar(select(User).where(User.telegram_user_id == sender_id))
    db.add(
        Membership(
            id=uuid.uuid4(), user_id=user.id, group_id=group.id, role="headman", status="active"
        )
    )
    await db.flush()
    # The bot sets the group's whole working environment up now rather than on its first message.
    await ensure_profile(db, group.id, now)
    await bot_start.connected(db, claim, settings, group)
