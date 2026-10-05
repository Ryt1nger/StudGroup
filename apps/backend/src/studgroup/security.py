"""Telegram bootstrap verification and opaque session credentials."""

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InvalidInitData(ValueError):
    code = "initdata_invalid"


class ExpiredInitData(InvalidInitData):
    code = "initdata_expired"


@dataclass(frozen=True)
class TelegramIdentity:
    telegram_user_id: int
    display_name: str
    username: str | None


def verify_init_data(raw: str, bot_token: str, *, now: int, max_age: int = 600) -> TelegramIdentity:
    if not bot_token or not raw or len(raw) > 16384:
        raise InvalidInitData()
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        fields = dict(pairs)
        if len(fields) != len(pairs):
            raise InvalidInitData()
        supplied_hash = fields.pop("hash")
        data_check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        key = hmac.digest(b"WebAppData", bot_token.encode(), "sha256")
        expected = hmac.new(key, data_check.encode(), "sha256").hexdigest()
        if not hmac.compare_digest(expected, supplied_hash):
            raise InvalidInitData()
        issued = int(fields["auth_date"])
        if issued > now + 30:
            raise InvalidInitData()
        if now - issued > max_age:
            raise ExpiredInitData()
        user = json.loads(fields["user"])
        user_id = user["id"]
        first = user["first_name"]
        username = user.get("username")
        last = user.get("last_name", "")
        if type(user_id) is not int or user_id <= 0 or not isinstance(first, str):
            raise InvalidInitData()
        if not isinstance(last, str) or (username is not None and not isinstance(username, str)):
            raise InvalidInitData()
        return TelegramIdentity(user_id, f"{first} {last}".strip(), username)
    except InvalidInitData:
        raise
    except (ValueError, KeyError, TypeError):
        raise InvalidInitData() from None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)
