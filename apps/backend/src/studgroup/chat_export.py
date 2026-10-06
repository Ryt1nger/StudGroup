"""Read-only Telegram HTML import preview. Never extract or execute archive content."""

import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

MAX_PAGE_BYTES = 8_000_000
MAX_TOTAL_BYTES = 32_000_000
MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


class InvalidExport(ValueError):
    """Safe error code, never containing message text."""


@dataclass(frozen=True)
class ExportMessage:
    message_id: int
    message_date: datetime
    text: str
    reply_to_message_id: int | None
    has_media: bool


def html_date(value: str, timezone: ZoneInfo) -> datetime:
    match = re.fullmatch(r"(\d+) (\w+) (\d+), (\d{2}):(\d{2}):(\d{2})", value)
    if not match or match[2] not in MONTHS:
        raise InvalidExport("unsupported_date_format")
    try:
        return datetime(
            int(match[3]),
            MONTHS[match[2]],
            int(match[1]),
            int(match[4]),
            int(match[5]),
            int(match[6]),
            tzinfo=timezone,
        )
    except ValueError:
        raise InvalidExport("invalid_date") from None


class TelegramHTML(HTMLParser):
    def __init__(self, timezone: ZoneInfo):
        super().__init__(convert_charrefs=True)
        self.timezone = timezone
        self.stack: list[tuple[str, set[str]]] = []
        self.current: dict | None = None
        self.message_depth = 0
        self.messages: list[ExportMessage] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = set(attrs.get("class", "").split())
        if tag == "div" and {"message", "default"} <= classes:
            if self.current is not None:
                raise InvalidExport("nested_message")
            match = re.fullmatch(r"message(\d+)", attrs.get("id", ""))
            if not match:
                raise InvalidExport("missing_message_id")
            self.current = {
                "id": int(match[1]),
                "date": None,
                "text": [],
                "reply": None,
                "media": False,
            }
            self.message_depth = len(self.stack)
        if self.current is not None:
            if tag == "div" and "date" in classes and self.current["date"] is None:
                self.current["date"] = html_date(attrs.get("title", ""), self.timezone)
            if "media_wrap" in classes:
                self.current["media"] = True
            if tag == "a" and any("reply_to" in c for _, c in self.stack):
                match = re.fullmatch(r"#go_to_message(\d+)", attrs.get("href", ""))
                if match:
                    self.current["reply"] = int(match[1])
            if tag == "br" and any("text" in c for _, c in self.stack):
                self.current["text"].append("\n")
        if tag not in {"br", "img", "meta", "link", "input", "hr", "source", "wbr"}:
            self.stack.append((tag, classes))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.handle_endtag(tag)

    def handle_data(self, data):
        if (
            self.current is not None
            and any("text" in c for _, c in self.stack)
            and not any(t in {"script", "style"} for t, _ in self.stack)
        ):
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1][0] != tag:
            raise InvalidExport("malformed_html")
        self.stack.pop()
        if self.current is not None and len(self.stack) == self.message_depth:
            current = self.current
            if current["date"] is None:
                raise InvalidExport("missing_message_date")
            self.messages.append(
                ExportMessage(
                    current["id"],
                    current["date"],
                    "".join(current["text"]).strip(),
                    current["reply"],
                    current["media"],
                )
            )
            self.current = None


def read_html_export(path: Path, *, timezone: str) -> list[ExportMessage]:
    """Timezone must be supplied explicitly: this HTML format has no UTC offset/chat ID.

    Returned objects are candidates for onboarding validation, not published cards.
    Avatar files, JS, CSS, media and service events are never read or executed.
    """
    zone = ZoneInfo(timezone)
    try:
        with ZipFile(path) as archive:
            pages = [
                entry
                for entry in archive.infolist()
                if not entry.filename.startswith("__MACOSX/")
                and re.fullmatch(r"messages\d*\.html", PurePosixPath(entry.filename).name)
            ]
            if not pages:
                raise InvalidExport("no_html_messages")
            if len({str(PurePosixPath(p.filename).parent) for p in pages}) != 1:
                raise InvalidExport("multiple_chat_exports")
            if sum(p.file_size for p in pages) > MAX_TOTAL_BYTES:
                raise InvalidExport("export_too_large")
            messages = []
            for page in pages:
                name = PurePosixPath(page.filename)
                if name.is_absolute() or ".." in name.parts or page.file_size > MAX_PAGE_BYTES:
                    raise InvalidExport("unsafe_export_page")
                parser = TelegramHTML(zone)
                parser.feed(archive.read(page).decode("utf-8"))
                parser.close()
                if parser.current is not None or parser.stack:
                    raise InvalidExport("truncated_html")
                messages.extend(parser.messages)
    except (BadZipFile, UnicodeError, RuntimeError):
        raise InvalidExport("invalid_archive") from None
    if len({message.message_id for message in messages}) != len(messages):
        raise InvalidExport("duplicate_message_id")
    return sorted(messages, key=lambda message: (message.message_date, message.message_id))
