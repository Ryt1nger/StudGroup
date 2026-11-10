"""Real PostgreSQL webhook and membership regression for automatic pilot invitation recovery.

Only the Telegram getChatMember response is stubbed. PostgreSQL, ORM writes,
webhook routing, profiles and invitations use the full application code.
"""

import test_bot_start as bot
from sqlalchemy import select
from test_postgres_student_card_api import pytestmark  # noqa: F401

from studgroup.models import Membership

pytest_plugins = ["test_postgres_student_card_api"]


def promote_headman(postgres_client):
    async def promote(db):
        member = await db.scalar(select(Membership))
        member.role = "headman"

    bot.run(postgres_client, promote)


def test_postgres_two_live_groups_enroll_only_verified_student(postgres_client, monkeypatch):
    promote_headman(postgres_client)
    bot.test_two_live_groups_select_only_verified_telegram_group(postgres_client, monkeypatch)


def test_postgres_two_verified_groups_never_guess_or_grant_access(postgres_client, monkeypatch):
    promote_headman(postgres_client)
    bot.test_two_verified_groups_never_guess_or_grant_student_access(
        postgres_client, monkeypatch
    )
