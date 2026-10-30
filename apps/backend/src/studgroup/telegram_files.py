"""Files that stay in Telegram: a card material whose bytes are fetched from Telegram on demand.

The headman sends the file to the bot; only the Telegram ``file_id`` is stored, as the material's
``url`` in the form ``tgfile:<kind>:<size>:<file_id>`` (the title is the file name). That keeps
merge, convert and undo, which copy ``title``/``url`` as they are, working without a schema change.

Students never see the ``file_id`` or the bot token. The card API returns a short-lived signed
path ``/v1/files/<material id>?exp=..&sig=..`` instead; ``GET /v1/files/...`` verifies it and
streams the file from Telegram (the Bot API serves files up to 20 MB).
"""

import hashlib
import hmac
import mimetypes
import time
import uuid
from typing import Annotated
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import MaterialLink

router = APIRouter(prefix="/v1")

PREFIX = "tgfile:"
KINDS = {"photo", "document", "video", "audio", "voice", "animation"}
MAX_BYTES = 20 * 1024 * 1024  # Bot API getFile limit
LINK_LIFETIME = 6 * 3600
INLINE_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/gif", "image/webp"}

_secret = b""


def configure(secret):
    """Key of the link signatures; derived from the webhook secret so no new variable is needed."""
    global _secret
    _secret = hashlib.sha256(b"studgroup-file-links:" + (secret or "").encode()).digest()


def encode(kind, size, file_id):
    return f"{PREFIX}{kind}:{size or 0}:{file_id}"


def decode(url):
    """(kind, size, file_id) of a stored file url, or None for an ordinary link."""
    if not url.startswith(PREFIX):
        return None
    kind, size, file_id = url[len(PREFIX) :].split(":", 2)
    if kind not in KINDS or not file_id:
        return None
    return kind, int(size) or None, file_id


def _signature(material_id, expires):
    return hmac.new(_secret, f"{material_id}.{expires}".encode(), hashlib.sha256).hexdigest()


def signed_path(material_id, now=None):
    expires = int((time.time() if now is None else now) + LINK_LIFETIME)
    return f"/v1/files/{material_id}?exp={expires}&sig={_signature(material_id, expires)}"


def present(row):
    """API shape of one stored material (an ordinary link, or a Telegram file with a signed path)."""
    file = decode(row.url)
    if file is None:
        return {"id": row.id, "title": row.title, "url": row.url}
    kind, size, _ = file
    return {
        "id": row.id,
        "title": row.title,
        "url": signed_path(row.id),
        "kind": kind,
        "file_name": row.title,
        "mime_type": mimetypes.guess_type(row.title)[0],
        "size_bytes": size,
    }


def verify(material_id, expires, signature):
    if not _secret or expires < time.time():
        return False
    return hmac.compare_digest(signature, _signature(material_id, expires))


@router.get("/files/{material_id}", include_in_schema=False)
async def download(
    material_id: uuid.UUID,
    exp: int,
    sig: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
):
    if not verify(material_id, exp, sig):
        raise ApiError("not_found", "Ссылка недействительна или устарела", 404)
    row = await db.get(MaterialLink, material_id)
    file = decode(row.url) if row else None
    if file is None:
        raise ApiError("not_found", "Файл не найден", 404)
    token = request.app.state.settings.telegram_bot_token
    try:
        async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
            info = (
                await client.post(
                    f"https://api.telegram.org/bot{token}/getFile", json={"file_id": file[2]}
                )
            ).json()
            path = info.get("result", {}).get("file_path") if info.get("ok") else None
            if not path:
                raise ApiError("not_found", "Telegram больше не отдаёт этот файл", 404)
            content = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
            if content.status_code != 200 or len(content.content) > MAX_BYTES:
                raise ApiError("service_unavailable", "Не удалось получить файл", 503)
    except httpx.HTTPError:
        raise ApiError("service_unavailable", "Не удалось получить файл", 503) from None
    mime = mimetypes.guess_type(row.title)[0] or mimetypes.guess_type(path)[0]
    mime = mime or "application/octet-stream"
    disposition = "inline" if mime in INLINE_TYPES else "attachment"
    return Response(
        content.content,
        media_type=mime if mime in INLINE_TYPES else "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(row.title)}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )
