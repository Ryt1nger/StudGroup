"""Real PostgreSQL invitation screen flow; only Telegram transport is simulated."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

from test_bot_start import labels, last, tap
from test_headman import actor, deliver, run
from test_headman import foreman as foreman  # noqa: PLC0414 -- pytest fixture

from studgroup import headman_domain as domain
from studgroup.models import GroupInvitation
from studgroup.security import token_hash

pytest_plugins = ["test_schedule_api"]


def configure(client):
    settings = client.app.state.settings
    settings.telegram_bot_token = "123456:pilot-test-secret"
    settings.bot_username = "studgroup_rf_bot"
    return settings


def active(client):
    async def read(db):
        _, _, group = await actor(db)
        invite = await domain.active_group_invite(db, group.id)
        return invite

    return run(client, read)


def link_from_panel(client):
    return next(
        line for line in last(client)["text"].splitlines()
        if line.startswith("https://t.me/studgroup_rf_bot?start=invite_")
    )


def test_invitation_screen_create_share_reopen_without_rotation(foreman):
    settings = configure(foreman)
    deliver(foreman, "/start")
    tap(foreman, "Пригласить участников", uid=1)
    assert "Активной пригласительной ссылки пока нет" in last(foreman)["text"]
    assert "Создать ссылку" in labels(foreman)
    assert active(foreman) is None

    tap(foreman, "Создать ссылку", uid=1)
    url = link_from_panel(foreman)
    invitation = active(foreman)
    code = url.split("invite_", 1)[1]
    assert invitation is not None
    assert token_hash(code) == invitation.token_hash
    assert invitation.token_ciphertext is not None
    assert code not in invitation.token_ciphertext
    assert domain.recover_group_invite_code(invitation, settings.telegram_bot_token) == code
    assert "Переслать приглашение" in labels(foreman)
    assert "Скопировать ссылку" in labels(foreman)

    buttons = last(foreman)["reply_markup"]["inline_keyboard"]
    share = next(button for row in buttons for button in row if button["text"] == "Переслать приглашение")
    query = parse_qs(urlsplit(share["url"]).query)
    assert urlsplit(share["url"]).hostname == "t.me"
    assert query["url"] == [url]
    assert "StudGroup" in query["text"][0]
    copied = next(button for row in buttons for button in row if button["text"] == "Скопировать ссылку")
    assert copied["copy_text"]["text"] == url

    deliver(foreman, "/start")
    tap(foreman, "Пригласить участников", uid=1)
    assert link_from_panel(foreman) == url
    same = active(foreman)
    assert same.token_hash == invitation.token_hash
    assert not same.revoked
    assert domain.recover_group_invite_code(same, settings.telegram_bot_token) == code


def test_legacy_hash_only_link_requires_explicit_confirmed_replacement(foreman):
    configure(foreman)

    async def create_legacy(db):
        _, member, group = await actor(db)
        return await domain.issue_group_link(db, group, member)

    old = run(foreman, create_legacy)
    deliver(foreman, "/start")
    tap(foreman, "Пригласить участников", uid=1)
    assert "Исходный токен восстановить невозможно" in last(foreman)["text"]
    assert "Заменить ссылку" in labels(foreman)
    assert "Переслать приглашение" not in labels(foreman)
    assert active(foreman).token_hash == token_hash(old)
    assert not active(foreman).revoked

    tap(foreman, "Заменить ссылку", uid=1)
    assert "Да, заменить ссылку" in labels(foreman)
    assert active(foreman).token_hash == token_hash(old)
    tap(foreman, "Отмена", uid=1)
    assert active(foreman).token_hash == token_hash(old)
    assert not active(foreman).revoked

    tap(foreman, "Заменить ссылку", uid=1)
    tap(foreman, "Да, заменить ссылку", uid=1)
    new_url = link_from_panel(foreman)
    assert old not in new_url
    assert "Переслать приглашение" in labels(foreman)

    async def old_revoked(db):
        return (await db.get(GroupInvitation, token_hash(old))).revoked

    assert run(foreman, old_revoked)
    assert active(foreman).token_hash == token_hash(new_url.split("invite_", 1)[1])


def test_expired_shared_link_is_not_shown_as_active(foreman):
    configure(foreman)
    deliver(foreman, "/start")
    tap(foreman, "Пригласить участников", uid=1)
    tap(foreman, "Создать ссылку", uid=1)
    old_url = link_from_panel(foreman)

    async def expire(db):
        invitation = await db.get(GroupInvitation, token_hash(old_url.split("invite_", 1)[1]))
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    run(foreman, expire)
    deliver(foreman, "/start")
    tap(foreman, "Пригласить участников", uid=1)
    assert "Создать ссылку" in labels(foreman)
    assert "Переслать приглашение" not in labels(foreman)
    tap(foreman, "Создать ссылку", uid=1)
    assert link_from_panel(foreman) != old_url


def test_cabinet_invite_entry_uses_same_persistent_screen(foreman):
    configure(foreman)
    deliver(foreman, "/st")
    assert "Пригласить участников" in labels(foreman)
    tap(foreman, "Пригласить участников", uid=1)
    assert "Создать ссылку" in labels(foreman)
    tap(foreman, "Создать ссылку", uid=1)
    url = link_from_panel(foreman)
    assert "Переслать приглашение" in labels(foreman)
    assert "‹ В кабинет старосты" in labels(foreman)

    tap(foreman, "В кабинет старосты", uid=1)
    assert "Староста · " in last(foreman)["text"]
    tap(foreman, "Пригласить участников", uid=1)
    assert link_from_panel(foreman) == url
