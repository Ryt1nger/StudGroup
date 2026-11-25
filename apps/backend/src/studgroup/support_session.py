"""Short-lived, owner-authorized support capabilities, never persisted as student sessions."""

import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Membership, User, WebSession
from studgroup.security import token_hash

PREFIX = "sgsupport1."
LIFETIME = timedelta(minutes=15)
LOGGER = logging.getLogger(__name__)
ID = r"[0-9a-fA-F-]{36}"
READ = (
    r"/v1/(today|schedule|ranepa-schedule|homework|deadlines|subjects|profile|subgroups|group-events|notifications)",
    rf"/v1/(homework|deadlines|group-events)/{ID}",
    r"/v1/lessons/[^/]+/subject",
    r"/v1/legal/status",
    r"/v1/staff/(schedule|no-lesson-days|deadline-queue)",
    rf"/v1/staff/items/{ID}",
)
WRITE = {
    "PATCH": (
        r"/v1/profile",
        rf"/v1/(homework|deadlines)/{ID}",
        r"/v1/staff/schedule/lessons/[^/]+",
        r"/v1/staff/group/settings",
    ),
    "PUT": (
        rf"/v1/homework/{ID}/completion",
        rf"/v1/subgroups/{ID}",
        rf"/v1/staff/items/{ID}/locks",
    ),
    "POST": (
        r"/v1/(homework|deadlines)",
        r"/v1/staff/(schedule/lessons|no-lesson-days)",
        rf"/v1/staff/items/{ID}/deadline",
        rf"/v1/card-removals/{ID}/undo",
    ),
    "DELETE": (
        rf"/v1/(homework|deadlines)/{ID}",
        r"/v1/staff/no-lesson-days/\d{4}-\d{2}-\d{2}",
    ),
}


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def allowed(mode, method, path):
    if method in {"GET", "HEAD"}:
        patterns = READ
    else:
        patterns = WRITE.get(method, ()) if mode == "edit" else ()
    return any(re.fullmatch(pattern, path) for pattern in patterns)


def sign(payload, secret):
    return hmac.new(
        secret.encode(), ("studgroup-support-v1:" + payload).encode(), hashlib.sha256
    ).hexdigest()


def issue(settings, parent, owner, member, mode, now):
    secret = settings.telegram_webhook_secret
    if not secret:
        raise ValueError("support_secret_missing")
    expires = min(now + LIFETIME, utc(parent.expires_at))
    claims = {
        "owner": str(owner.id),
        "parent": parent.token_hash,
        "member": str(member.id),
        "user": str(member.user_id),
        "group": str(member.group_id),
        "mode": mode,
        "exp": int(expires.timestamp()),
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return PREFIX + payload + "." + sign(payload, secret), expires


async def validate(db, settings, token):
    secret = settings.telegram_webhook_secret
    if not secret or len(token) > 2048 or not token.startswith(PREFIX):
        return None
    try:
        payload, signature = token[len(PREFIX) :].split(".")
        if not hmac.compare_digest(sign(payload, secret), signature):
            return None
        claims = json.loads(base64.urlsafe_b64decode(payload))
        expires = datetime.fromtimestamp(claims["exp"], UTC)
        owner_id = UUID(claims["owner"])
        member_id = UUID(claims["member"])
        user_id = UUID(claims["user"])
        group_id = UUID(claims["group"])
        mode = claims["mode"]
        parent_hash = claims["parent"]
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    now = datetime.now(UTC)
    if expires <= now or mode not in {"view", "edit"}:
        return None
    parent = await db.get(WebSession, parent_hash)
    owner = await db.get(User, owner_id)
    member = await db.get(Membership, member_id)
    group = await db.get(Group, group_id)
    if (
        parent is None
        or parent.user_id != owner_id
        or utc(parent.expires_at) <= now
        or owner is None
        or owner.telegram_user_id != settings.bot_admin_user_id
        or member is None
        or member.user_id != user_id
        or member.group_id != group_id
        or member.status != "active"
        or group is None
        or group.status != "active"
        or not group.pilot_authorized
    ):
        return None
    return {
        "owner_id": owner_id,
        "user_id": user_id,
        "group_id": group_id,
        "membership_id": member_id,
        "mode": mode,
        "credential": WebSession(
            token_hash=token_hash(token),
            user_id=user_id,
            membership_id=member_id,
            expires_at=expires,
        ),
    }


class SupportDatabase(AsyncSession):
    """Virtual credential is only available inside a validated request."""

    async def get(self, entity, ident, **kwargs):
        support = self.info.get("support")
        if support and entity is WebSession and ident == support["credential"].token_hash:
            return support["credential"]
        return await super().get(entity, ident, **kwargs)


def context(db):
    return db.info.get("support")


def editing(db, group_id):
    support = context(db)
    return bool(support and support["mode"] == "edit" and support["group_id"] == group_id)


def editorial_member(member):
    # Capability for domain rules, not a persisted role change.
    return SimpleNamespace(
        id=member.id,
        user_id=member.user_id,
        group_id=member.group_id,
        status=member.status,
        role="headman",
    )
