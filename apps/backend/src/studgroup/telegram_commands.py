"""Publish the two supported Telegram commands without exposing admin to students.

Run explicitly with: uv run python -m studgroup.telegram_commands
The command menu is never changed during application startup or during tests.
"""

import asyncio

from studgroup.bot_admin import telegram_request
from studgroup.main import Settings

STUDENT_COMMANDS = [{"command": "start", "description": "Открыть StudGroup"}]
OWNER_COMMANDS = [
    *STUDENT_COMMANDS,
    {"command": "admin", "description": "Администрирование"},
]


async def configure(settings):
    """Keep regular chats limited to /start; /admin is visible only to the owner."""
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required")
    result = await telegram_request(
        settings,
        "setMyCommands",
        {"commands": STUDENT_COMMANDS, "scope": {"type": "default"}},
    )
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected default command menu")
    if settings.bot_admin_user_id:
        result = await telegram_request(
            settings,
            "setMyCommands",
            {
                "commands": OWNER_COMMANDS,
                "scope": {"type": "chat", "chat_id": settings.bot_admin_user_id},
            },
        )
        if not result.get("ok"):
            raise RuntimeError("Telegram rejected owner command menu")
    return True


if __name__ == "__main__":
    asyncio.run(configure(Settings()))
