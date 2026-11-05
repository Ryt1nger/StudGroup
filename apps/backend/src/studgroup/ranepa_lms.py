"""Read-only RANEPA Moodle course-link discovery (no credentials persisted).

This is a *candidate* source adapter: the institution has not confirmed that
Moodle's standard REST web services are enabled for student accounts. Only
explicit Moodle course activities are returned, never guessed meeting URLs.
"""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

import httpx

BASE_URL = "https://lms.ranepa.ru"
REST_PATH = "/webservice/rest/server.php"
ACTIVITY_TYPES = frozenset({"url", "bigbluebuttonbn", "webinar", "zoom"})
LIVE_WORDS = ("лекц", "лек(", "вебинар", "дистант", "онлайн", "подключ", "заняти")


class RanepaLmsError(Exception):
    """Safe external-facing error; never includes an upstream URL or token."""


@dataclass(frozen=True, slots=True)
class LectureActivity:
    course_id: int
    module_id: int
    title: str
    section: str
    activity_type: str
    activity_url: str


def _positive_int(value: object) -> int | None:
    if type(value) is not int or value <= 0:
        return None
    return value


def _trusted_url(url: object, *, path: str, allowed_query: set[str]) -> dict[str, list[str]] | None:
    if not isinstance(url, str) or len(url) > 2048:
        return None
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname != "lms.ranepa.ru"
            or parts.port not in (None, 443)
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
            or parts.path != path
        ):
            return None
        query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
        if set(query) != allowed_query or any(len(v) != 1 for v in query.values()):
            return None
        return query
    except (ValueError, UnicodeError):
        return None


def parse_course_id(course_url: str) -> int:
    """Accept only the university's canonical course URL, no hidden auth parameters."""
    params = _trusted_url(course_url, path="/course/view.php", allowed_query={"id"})
    if params is None or not params["id"][0].isdigit() or int(params["id"][0]) == 0:
        raise ValueError("Некорректная ссылка на курс РАНХиГС")
    return int(params["id"][0])


def parse_lecture_activities(sections: object, *, course_id: int) -> list[LectureActivity]:
    """Normalize Moodle core_course_get_contents sections into safe navigation links.

    The returned URL opens an *activity in Moodle*, not necessarily the final
    conference room. Never deduplicate solely by title: different meetings often
    have the same teacher/lecture name.
    """
    if _positive_int(course_id) is None or not isinstance(sections, list):
        raise ValueError("Некорректный ответ СДО")
    result: list[LectureActivity] = []
    seen: set[str] = set()
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_title = section.get("name")
        section_title = section_title.strip()[:255] if isinstance(section_title, str) else ""
        modules = section.get("modules")
        if not isinstance(modules, list):
            continue
        for module in modules:
            if not isinstance(module, dict):
                continue
            kind = module.get("modname")
            mid = _positive_int(module.get("id"))
            label = module.get("name")
            if kind not in ACTIVITY_TYPES or mid is None or not isinstance(label, str):
                continue
            label = " ".join(label.split())[:255]
            if not label or module.get("visible", 1) in (0, False) or module.get(
                "uservisible", True
            ) in (0, False):
                continue
            if not any(word in (label + " " + section_title).casefold() for word in LIVE_WORDS):
                continue
            link = module.get("url")
            params = _trusted_url(
                link, path=f"/mod/{kind}/view.php", allowed_query={"id"}
            )
            if params is None or params["id"] != [str(mid)]:
                continue
            if link in seen:
                continue
            seen.add(link)
            result.append(
                LectureActivity(
                    course_id=course_id,
                    module_id=mid,
                    title=label,
                    section=section_title,
                    activity_type=kind,
                    activity_url=link,
                )
            )
    return result


async def fetch_lecture_activities(
    course_url: str, *, token: str, transport: httpx.AsyncBaseTransport | None = None
) -> list[LectureActivity]:
    """Try the official Moodle *read-only* web service with an already-authorized token.

    No password flow, token creation, login scraping, CAPTCHA bypass, cookie reuse,
    or writes. The caller must ensure that the account has permission for this course.
    If RANEPA does not expose this service, the caller gets a safe failure.
    """
    cid = parse_course_id(course_url)
    if not isinstance(token, str) or not token or len(token) > 512:
        raise ValueError("Нет разрешённого токена СДО")
    try:
        async with httpx.AsyncClient(
            base_url=BASE_URL, timeout=10.0, follow_redirects=False, transport=transport
        ) as client:
            response = await client.post(
                REST_PATH,
                data={
                    "wstoken": token,
                    "wsfunction": "core_course_get_contents",
                    "moodlewsrestformat": "json",
                    "courseid": str(cid),
                },
            )
            if response.is_redirect:
                raise RanepaLmsError("Вход через API СДО недоступен")
            response.raise_for_status()
            payload = response.json()
    except RanepaLmsError:
        raise
    except (httpx.HTTPError, ValueError):
        raise RanepaLmsError("Не удалось получить данные СДО") from None
    if isinstance(payload, dict) and ("exception" in payload or "errorcode" in payload):
        raise RanepaLmsError("СДО не разрешила чтение курса через API")
    if not isinstance(payload, list):
        raise RanepaLmsError("СДО вернула неожиданный формат ответа")
    return parse_lecture_activities(payload, course_id=cid)
