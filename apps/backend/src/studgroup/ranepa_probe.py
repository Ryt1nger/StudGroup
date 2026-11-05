"""Read-only probe of the public RANEPA schedule page; never writes calendar data."""

import hashlib
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from uuid import UUID

import httpx

from studgroup.ranepa_schedule import group_code, normalized

SCHEDULE_URL = "https://my.ranepa.ru/schedule/"
MAX_PAGE_BYTES = 2_000_000
TIME_RANGE = re.compile(r"\b\d{1,2}\s*:\s*\d{2}\s*-\s*\d{1,2}\s*:\s*\d{2}\b")


class SourceAccessError(RuntimeError):
    """A conservative, unambiguous reason that the page cannot be used as source."""


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "svg", "noscript"}:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "svg", "noscript"}:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if not self._skip:
            self.chunks.append(data)


@dataclass(frozen=True)
class PublicProbe:
    group_code: str
    internal_group_uuid: str
    pair_time_ranges: int
    page_sha256: str

    @property
    def has_lesson_candidates(self) -> bool:
        return self.pair_time_ranges > 0


async def probe_public(
    internal_group_uuid: UUID,
    expected_group_code: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> PublicProbe:
    """Test a verified group UUID without guessing IDs or defeating login/CAPTCHA."""
    code = group_code(expected_group_code)
    params = {"by": "group", "f": str(internal_group_uuid), "type": "schedule"}
    owned = client is None
    if owned:
        client = httpx.AsyncClient(
            timeout=15,
            trust_env=False,
            follow_redirects=False,
            headers={"Accept": "text/html"},
        )
    try:
        try:
            response = await client.get(SCHEDULE_URL, params=params)
        except httpx.HTTPError:
            raise SourceAccessError("source_unreachable") from None
        if response.status_code in {401, 403}:
            raise SourceAccessError("authentication_required")
        if response.status_code != 200:
            raise SourceAccessError("source_http_error")
        if len(response.content) > MAX_PAGE_BYTES:
            raise SourceAccessError("response_too_large")
        if "text/html" not in response.headers.get("content-type", "").lower():
            raise SourceAccessError("unexpected_response_type")
        parser = _VisibleText()
        parser.feed(response.text)
        text = " ".join(parser.chunks)
        clean = normalized(text)
        if "captcha" in clean or "капча" in clean or "проверка, что вы не робот" in clean:
            raise SourceAccessError("captcha_required")
        if normalized(code) not in clean:
            raise SourceAccessError("group_identity_not_confirmed")
        return PublicProbe(
            group_code=code,
            internal_group_uuid=str(internal_group_uuid),
            pair_time_ranges=len(TIME_RANGE.findall(clean)),
            page_sha256=hashlib.sha256(response.content).hexdigest(),
        )
    finally:
        if owned:
            await client.aclose()
