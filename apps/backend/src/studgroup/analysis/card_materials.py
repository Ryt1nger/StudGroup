"""Study materials of a card, derived only from stored Telegram metadata.

C+ never reads file contents: a material is a message's attachment metadata or a link written in
a message. Only messages cited as evidence, direct replies to them and members of the same media
group count as confirmed; mere adjacency is reported separately as an unconfirmed candidate.
"""

import re
from datetime import timedelta

from studgroup.analysis.review_text import telegram_link

URL = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
CLOUD_HOSTS = (
    "drive.google.",
    "docs.google.",
    "disk.yandex.",
    "yadi.sk",
    "cloud.mail.ru",
    "dropbox.com",
    "onedrive.",
    "1drv.ms",
    "notion.",
    "miro.com",
    "figma.com",
)
LMS_HINTS = ("moodle", "sdo.", "lms.", "edu.", "eios", "dist.", "stepik", "coursera")
GROUP_LABELS = {"photo": "Фото", "file": "Файлы", "media": "Видео и аудио", "link": "Ссылки"}
CANDIDATE_WINDOW = timedelta(minutes=15)
FILE_KINDS = {"document"}
MEDIA_KINDS = {"video", "audio", "voice", "animation"}


def link_kind(url):
    host = re.sub(r"^https?://", "", url.casefold()).split("/", 1)[0]
    if any(part in host for part in CLOUD_HOSTS):
        return "cloud"
    if any(part in host for part in LMS_HINTS):
        return "lms"
    if host.startswith(("t.me", "telegram.me")):
        return "telegram"
    return "site"


def _group_of(kind):
    if kind == "photo":
        return "photo"
    if kind in FILE_KINDS:
        return "file"
    return "media"


def _from_message(message, chat_id, via):
    items = []
    link = telegram_link(chat_id, message.telegram_message_id)
    for attachment in message.attachments:
        items.append(
            {
                "group": _group_of(attachment.kind),
                "kind": attachment.kind,
                "title": attachment.file_name or {"photo": "Фото"}.get(attachment.kind, "Файл"),
                "mime_type": attachment.mime_type,
                "message_id": message.telegram_message_id,
                "telegram_link": link,
                "via": via,
                "contents_read": False,
            }
        )
    for url in dict.fromkeys(URL.findall(message.text or "")):
        url = url.rstrip(".,;:!?")
        items.append(
            {
                "group": "link",
                "kind": link_kind(url),
                "title": url,
                "url": url,
                "message_id": message.telegram_message_id,
                "telegram_link": link,
                "via": via,
                "contents_read": False,
            }
        )
    return items


def derive_materials(cited, messages, chat_id):
    """Return (confirmed materials, unconfirmed candidates) for one card, deduplicated."""
    cited_ids = {row.telegram_message_id for row in cited}
    albums = {a.media_group_id for row in cited for a in row.attachments if a.media_group_id}
    confirmed, candidates, seen = [], [], set()

    def add(target, item):
        key = (item["message_id"], item["group"], item["title"], item.get("url"))
        if key not in seen:
            seen.add(key)
            target.append(item)

    for row in messages:
        via = None
        if row.telegram_message_id in cited_ids:
            via = "cited"
        elif row.reply_to_message_id in cited_ids:
            via = "reply"
        elif albums and any(a.media_group_id in albums for a in row.attachments):
            via = "album"
        if via:
            for item in _from_message(row, chat_id, via):
                add(confirmed, item)
    nearby = []
    for row in messages:
        if row.telegram_message_id in cited_ids or not row.attachments:
            continue
        if any(item["message_id"] == row.telegram_message_id for item in confirmed):
            continue
        close = any(
            abs(row.message_date - source.message_date) <= CANDIDATE_WINDOW
            and row.topic_id == source.topic_id
            for source in cited
        )
        if close:
            nearby.append(row)
    for row in nearby:
        for item in _from_message(row, chat_id, "adjacent_unconfirmed"):
            add(candidates, item)
    return confirmed, candidates


def group_counts(materials):
    counts = {}
    for item in materials:
        counts[item["group"]] = counts.get(item["group"], 0) + 1
    return {GROUP_LABELS[key]: counts[key] for key in GROUP_LABELS if key in counts}


def summary_label(materials):
    counts = group_counts(materials)
    return ", ".join(f"{label} ({count})" for label, count in counts.items())
