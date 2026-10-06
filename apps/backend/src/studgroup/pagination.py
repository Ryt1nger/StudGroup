"""Short-lived list cursors, authenticated by the current session credential."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from uuid import UUID

from studgroup.api import ApiError


def encode_cursor(group_id, filter_name, at, last_created, last_id, credential):
    data = json.dumps(
        [str(group_id), filter_name, at.isoformat(), last_created.isoformat(), str(last_id)],
        separators=(",", ":"),
    ).encode()
    payload = base64.urlsafe_b64encode(data).decode().rstrip("=")
    signature = hmac.new(credential.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def decode_cursor(value, group_id, filter_name, now, credential):
    try:
        payload, signature = value.split(".")
        expected = hmac.new(credential.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        group, category, at, created, last_id = json.loads(
            base64.b64decode(payload + "=" * (-len(payload) % 4), altchars=b"-_", validate=True)
        )
        at, created = datetime.fromisoformat(at), datetime.fromisoformat(created)
        if (
            group != str(group_id)
            or category != filter_name
            or at.tzinfo is None
            or created.tzinfo is None
            or not at <= now < at + timedelta(minutes=15)
            or created > at
        ):
            raise ValueError()
        return at, created, UUID(last_id)
    except (ValueError, TypeError, UnicodeError):
        raise ApiError("invalid_request", "Обновите список заданий", 422) from None
