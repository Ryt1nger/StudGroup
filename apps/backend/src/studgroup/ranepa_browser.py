"""Read-only personal-cabinet browser port with one automatic re-login."""

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from time import monotonic
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from studgroup.ranepa_cabinet import MonthCard, extract_cabinet_month, extract_cabinet_week
from studgroup.ranepa_crawl_trace import CrawlTrace
from studgroup.ranepa_failure_diagnostics import (
    diagnose_browser_state,
    diagnose_exception,
    diagnose_http_response,
)
from studgroup.ranepa_link_states import LinkState
from studgroup.ranepa_lms import (
    MAX_CANDIDATES,
    MAX_DETAIL_PAGES,
    LinkChoice,
    course_detail_pages,
    course_links,
    dashboard_courses,
    matching_courses,
    meeting_url,
    select_lesson_link,
)
from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot, group_code
from studgroup.ranepa_ui import page_status

SCHEDULE = "https://my.ranepa.ru/lk/student/schedule"
HOST = "my.ranepa.ru"
LOGGER = logging.getLogger(__name__)
browser_trace: ContextVar[CrawlTrace | None] = ContextVar("lk_trace", default=None)


def _login_stage(step: str) -> None:
    trace = browser_trace.get()
    if trace is not None:
        trace.record(step)


@dataclass(frozen=True)
class BrowserResult:
    status: str
    snapshot: WeekSnapshot | None = None
    sdo_exits: tuple[str | None, ...] = ()
    diagnostic: str | None = None


@dataclass(frozen=True)
class BrowserMonthResult:
    status: str
    cards: tuple[MonthCard, ...] = ()
    diagnostic: str | None = None


async def _state(page) -> str:
    if urlsplit(page.url).hostname != HOST:
        return "unexpected_redirect"
    body = await page.locator("body").inner_text(timeout=8000)
    password_input = await page.locator('input[type="password"]').count() > 0
    # A short spinner page may contain StormWall's get_jhash JavaScript but
    # no visible CAPTCHA. Give its redirect time to run; do not attempt login.
    js_challenge = False
    title = ""
    if not password_input and len(body) < 500:
        html = await page.content()
        js_challenge = "get_jhash" in html and ("__js_p_" in html or "__jua_" in html)
        title = await page.title()
    return page_status(
        page.url,
        body,
        password_input=password_input,
        title=title,
        js_challenge=js_challenge,
    )


async def _wait_visible_state(page) -> str:
    state = "empty_page"
    for _ in range(100):
        state = await _state(page)
        if state not in {"empty_page", "challenge_pending"}:
            break
        await page.wait_for_timeout(250)
    return state


async def wait_for_captcha_resolution(page, *, timeout_seconds: float = 6.0) -> str:
    """Observe the same LK page after a submitted drag; do not resubmit.

    A disappearance is only a candidate state change. The normal month/week
    reader must still verify the identity and schedule before data is trusted.
    Emit fixed stages only; never log page content or authorization data.
    """
    state = "captcha_required"
    try:
        async with asyncio.timeout(timeout_seconds):
            while True:
                state = await _state(page)
                if state == "rendered":
                    return "challenge_page_changed_unverified"
                if state == "login_required":
                    return "challenge_login_visible_after_drag"
                if state == "unexpected_redirect":
                    return "challenge_redirect_after_drag"
                if state not in {"captcha_required", "empty_page", "challenge_pending"}:
                    return "challenge_other_state_after_drag"
                await page.wait_for_timeout(250)
    except TimeoutError:
        return (
            "challenge_still_visible_after_wait"
            if state == "captcha_required"
            else "challenge_unresolved_after_wait"
        )
    except Exception:  # noqa: BLE001
        # Navigation can invalidate locators; do not emit exception details.
        return "challenge_state_unavailable"


async def _sign_in(page, login: str, password: str) -> str:
    _login_stage("lk_form_visible")
    await page.get_by_placeholder("Логин").fill(login, timeout=8000)
    _login_stage("lk_login_filled")
    await page.locator('input[type="password"]').fill(password, timeout=8000)
    _login_stage("lk_password_filled")
    _login_stage("lk_submit_requested")
    await page.get_by_role("button", name="Войти", exact=True).click(timeout=8000)
    _login_stage("lk_form_submitted")
    # Three observation windows, not three password submissions.
    for check in range(300):
        if check in {100, 200}:
            _login_stage("lk_response_recheck")
        state = await _state(page)
        if state not in {"login_required", "empty_page", "challenge_pending"}:
            step = {
                "login_failed": "lk_explicit_rejection",
                "captcha_required": "lk_captcha_required",
                "unexpected_redirect": "lk_unexpected_redirect",
                "rendered": "lk_rendered_after_submit",
            }.get(state)
            if step is not None:
                _login_stage(step)
            return state
        await page.wait_for_timeout(250)
    _login_stage(
        {
            "login_required": "lk_form_still_visible",
            "empty_page": "lk_empty_after_submit",
            "challenge_pending": "lk_challenge_pending",
        }[state]
    )
    # A stalled login form or loading spinner is not an explicit rejection.
    return "challenge_pending" if state == "challenge_pending" else "timeout"


NAVIGATION_CHECKS = 3
NETWORK_RECHECK_CODES = frozenset({"timeout", "dns", "connection", "http_5xx"})


async def _navigate_schedule(page) -> str:
    """Retry read-only navigation, not rejected passwords or access restrictions."""
    state = "timeout"
    for attempt in range(NAVIGATION_CHECKS):
        _login_stage("lk_open_requested")
        try:
            response = await page.goto(SCHEDULE, wait_until="domcontentloaded", timeout=30000)
            _login_stage("lk_page_loaded")
            problem = diagnose_http_response(response)
            state = problem or await _wait_visible_state(page)
            if state == "empty_page":
                state = "timeout"
        except Exception as error:  # noqa: BLE001 -- safe codes only
            state = diagnose_exception(error)
        if state not in NETWORK_RECHECK_CODES:
            return state
        if attempt + 1 < NAVIGATION_CHECKS:
            _login_stage("lk_connection_recheck")
            await page.wait_for_timeout(2000 * (attempt + 1))
    _login_stage("lk_connection_checks_exhausted")
    return state


async def _open(page, login: str, password: str) -> str:
    current = urlsplit(page.url)
    if current.hostname == HOST and current.path == urlsplit(SCHEDULE).path:
        state = await _wait_visible_state(page)
    else:
        state = await _navigate_schedule(page)
    if state == "login_required":
        state = await _sign_in(page, login, password)
        if state == "rendered":
            _login_stage("lk_schedule_requested")
            state = await _navigate_schedule(page)
    if state == "login_failed":
        _login_stage("lk_explicit_rejection")
    return state


async def _verify_group(page, code: str) -> bool:
    _login_stage("lk_group_verify_requested")
    avatar = page.locator(".mobile-header__avatar")
    await avatar.click(timeout=8000)
    active = page.locator(".avatar-navigation__role-btn.active")
    labels = await active.all_text_contents()
    await avatar.click(timeout=8000)
    expected = group_code(code).casefold()
    verified = len(labels) == 1 and labels[0].strip().casefold().startswith(expected + " ")
    if verified:
        _login_stage("lk_group_verified")
    return verified


async def _enrich(page, snapshot: WeekSnapshot) -> BrowserResult:
    """Read each detail popup, including stream label and SDO transition."""
    cards = page.locator(".schedule-week-card")
    if await cards.count() != len(snapshot.lessons):
        return BrowserResult("cabinet_card_count_mismatch")
    enriched = []
    exits = []
    for index, row in enumerate(snapshot.lessons):
        card = cards.nth(index)
        tooltip_id = await card.get_attribute("aria-describedby")
        if not tooltip_id or not tooltip_id.startswith("v-tooltip-"):
            return BrowserResult("cabinet_detail_missing")
        await card.click()
        detail = page.locator(f"#{tooltip_id}")
        await detail.wait_for(state="visible", timeout=5000)
        kind = " ".join((await detail.locator(".schedule-detail__type").inner_text()).split())
        values = {}
        for part in await detail.locator(".schedule-detail__row").all():
            label = " ".join(
                (await part.locator(".schedule-detail__row-title").inner_text()).split()
            )
            value = " ".join(
                (await part.locator(".schedule-detail__row-value").first.inner_text()).split()
            )
            values[label] = (
                value,
                await part.locator("a[href]").first.get_attribute("href")
                if await part.locator("a[href]").count()
                else None,
            )
        group_label = values.get("Группа", (None, None))[0]
        timing = values.get("Дата и время", (None, None))[0]
        if (
            not kind
            or not group_label
            or not timing
            or f"{row.starts:%H:%M} - {row.ends:%H:%M}" not in timing
            or str(row.day.day) not in timing
        ):
            return BrowserResult("cabinet_detail_mismatch")
        exit_url = None
        if row.room == "СДО":
            candidate = values.get("Аудитория", (None, None))[1]
            if candidate:
                parsed = urlsplit(urljoin(SCHEDULE, candidate))
                if (
                    parsed.scheme == "https"
                    and parsed.hostname == HOST
                    and parsed.path == "/moodle_out.php"
                ):
                    exit_url = urljoin(SCHEDULE, candidate)
        enriched.append(
            ExternalLesson(
                day=row.day,
                starts=row.starts,
                ends=row.ends,
                subject=row.subject,
                teacher=row.teacher,
                room=row.room,
                lesson_type=kind,
                subgroup_hint=group_label,
            )
        )
        exits.append(exit_url)
        await detail.locator(".schedule-detail__close-icon").click()
    return BrowserResult(
        "preview_only",
        WeekSnapshot(snapshot.group_code, snapshot.week_start, snapshot.week_end, tuple(enriched)),
        tuple(exits),
    )


async def read_week(
    page, login: str, password: str, code: str, monday: date, *, allow_empty: bool = False
) -> BrowserResult:
    """Read a target week through visible navigation; renew an expired session once."""
    if monday.weekday() != 0:
        raise ValueError("week_start_must_be_monday")
    for attempt in range(2):
        try:
            state = await _open(page, login, password)
            if state != "rendered":
                return BrowserResult(state, diagnostic=diagnose_browser_state(state))
            if not await _verify_group(page, code):
                return BrowserResult("identity_unverified")
            await page.get_by_role("button", name="Мое расписание", exact=True).click()
            await page.get_by_role("button", name="Неделя", exact=True).click()
            await page.get_by_role("button", name="Сегодня", exact=True).click()
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            current_monday = today - timedelta(days=today.weekday())
            distance = (monday - current_monday).days // 7
            if abs(distance) > 8:
                return BrowserResult("outside_navigation_window")
            arrow = page.locator("button.schedule__navigation-button--icon").nth(
                1 if distance > 0 else 0
            )
            for _ in range(abs(distance)):
                await arrow.click()
            # Navigation changes the URL-neutral calendar grid asynchronously.
            # A previous week's headers may remain visible briefly after a
            # navigation click; wait for the requested dates, never trust them.
            last_week_error = "cabinet_week_incomplete"
            for _ in range(40):
                state = await _state(page)
                if state == "captcha_required":
                    return BrowserResult("captcha_required")
                if state == "login_required":
                    break
                if state == "challenge_pending":
                    await page.wait_for_timeout(250)
                    continue
                try:
                    snapshot = extract_cabinet_week(
                        await page.content(), code, monday, allow_empty=allow_empty
                    )
                    return await _enrich(page, snapshot)
                except SourceAccessError as error:
                    if str(error) not in {
                        "cabinet_week_loading",
                        "cabinet_week_grid_missing",
                        "cabinet_week_incomplete",
                        "cabinet_time_missing",
                        "cabinet_card_incomplete",
                        "cabinet_day_mismatch",
                        "cabinet_weekday_mismatch",
                        "empty_source_requires_review",
                    }:
                        return BrowserResult(str(error))
                    last_week_error = str(error)
                await page.wait_for_timeout(250)
            final_state = await _state(page)
            if final_state == "captcha_required":
                return BrowserResult("captcha_required")
            if final_state == "challenge_pending":
                return BrowserResult("source_unavailable", diagnostic="challenge_pending")
            if final_state != "login_required":
                return BrowserResult(last_week_error)
        except Exception as error:  # noqa: BLE001
            # Exception content may contain session URLs; keep only the safe code.
            return BrowserResult("source_unavailable", diagnostic=diagnose_exception(error))
        if attempt == 1:
            break
    _login_stage("lk_session_expired")
    return BrowserResult("source_unavailable", diagnostic="session_expired")


async def read_month(
    page, login: str, password: str, code: str, year: int, month: int
) -> BrowserMonthResult:
    """Read month index through the same authenticated interface, without DB writes."""
    for attempt in range(2):
        try:
            state = await _open(page, login, password)
            if state != "rendered":
                return BrowserMonthResult(state, diagnostic=diagnose_browser_state(state))
            if not await _verify_group(page, code):
                return BrowserMonthResult("identity_unverified")
            await page.get_by_role("button", name="Мое расписание", exact=True).click()
            await page.get_by_role("button", name="Месяц", exact=True).click()
            await page.get_by_role("button", name="Сегодня", exact=True).click()
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            distance = (year - today.year) * 12 + month - today.month
            if abs(distance) > 2:
                return BrowserMonthResult("outside_navigation_window")
            arrow = page.locator("button.schedule__navigation-button--icon").nth(
                1 if distance > 0 else 0
            )
            for _ in range(abs(distance)):
                await arrow.click()
            for _ in range(60):
                state = await _state(page)
                if state == "captcha_required":
                    return BrowserMonthResult("captcha_required")
                if state == "login_required":
                    break
                if state == "challenge_pending":
                    await page.wait_for_timeout(250)
                    continue
                try:
                    return BrowserMonthResult(
                        "preview_only", extract_cabinet_month(await page.content(), year, month)
                    )
                except SourceAccessError as error:
                    if str(error) not in {
                        "cabinet_month_incomplete",
                        "cabinet_month_mismatch",
                        "cabinet_month_card_incomplete",
                        "empty_source_requires_review",
                    }:
                        return BrowserMonthResult(str(error))
                await page.wait_for_timeout(250)
            final_state = await _state(page)
            if final_state == "captcha_required":
                return BrowserMonthResult("captcha_required")
            if final_state == "challenge_pending":
                return BrowserMonthResult("source_unavailable", diagnostic="challenge_pending")
            if final_state != "login_required":
                return BrowserMonthResult("cabinet_month_incomplete")
        except Exception as error:  # noqa: BLE001
            return BrowserMonthResult("source_unavailable", diagnostic=diagnose_exception(error))
        if attempt == 1:
            break
    _login_stage("lk_session_expired")
    return BrowserMonthResult("source_unavailable", diagnostic="session_expired")


@dataclass
class SdoLinkResolution:
    """Backwards-compatible three-map result with private course candidates."""

    links: dict
    states: dict
    courses: dict
    candidates: dict

    def __iter__(self):
        yield self.links
        yield self.states
        yield self.courses


async def _expand_lms_sections(page) -> None:
    """Reveal only read-only Moodle course sections, not forms or activities."""
    if not hasattr(page, "evaluate"):
        return
    try:
        await page.evaluate(
            """() => {
                for (const item of document.querySelectorAll('main details:not([open])')) {
                    item.open = true;
                }
                for (const item of document.querySelectorAll(
                    'main .course-section button[aria-expanded="false"],' +
                    'main .course-section a[aria-expanded="false"],' +
                    'main button.section-collapser[aria-expanded="false"]'
                )) {
                    item.click();
                }
            }"""
        )
    except Exception:  # noqa: BLE001 -- optional progressive page reveal
        return


async def _read_course_candidates(page, context):
    """Bounded discovery on the course and linked same-origin text/book pages.

    Never open a conference, download files, submit forms, or follow redirects
    outside Moodle. Each page is read at most twice after a section expansion.
    """
    found = {}
    child_pages = {}
    verified = False
    for attempt in range(8):
        try:
            html = await page.content()
            current = course_links(html)
            verified = True
            for link in current:
                previous = found.get(link.url)
                if previous is None or len(link.label) > len(previous.label):
                    found[link.url] = link
            for url in course_detail_pages(html):
                child_pages[url] = None
        except SourceAccessError:
            pass
        if attempt == 0:
            await _expand_lms_sections(page)
        # Moodle can hydrate activity sections after the course shell renders.
        # Do not stop after the first link: later sections may contain OTHER
        # teachers' meetings. Keep the read bounded to eight observations.
        if attempt < 7:
            await page.wait_for_timeout(250)
    if not verified:
        return (), False
    nested_deadline = monotonic() + 8
    for url in tuple(child_pages)[:MAX_DETAIL_PAGES]:
        remaining = nested_deadline - monotonic()
        if remaining <= 0:
            break
        child = await context.new_page()
        try:
            await child.goto(
                url, wait_until="domcontentloaded", timeout=max(1, int(remaining * 1000))
            )
            final = urlsplit(child.url)
            if (
                final.scheme != "https"
                or final.hostname != "lms.ranepa.ru"
                or final.path
                not in {"/mod/page/view.php", "/mod/book/view.php", "/course/section.php"}
            ):
                continue
            await _expand_lms_sections(child)
            for _ in range(2):
                html = await child.content()
                for link in course_links(html, require_course=False):
                    previous = found.get(link.url)
                    if previous is None or len(link.label) > len(previous.label):
                        found[link.url] = link
                await child.wait_for_timeout(250)
        except Exception as error:  # noqa: BLE001 -- best-effort optional nested page
            # Log only the exception type: URLs can carry session data.
            LOGGER.debug("sdo_nested_page_unavailable reason=%s", type(error).__name__)
        finally:
            await child.close()
    return tuple(found.values())[:MAX_CANDIDATES], True


async def _shared_moodle_redirect(context, candidates):
    """Collapse duplicate Moodle URL activities only when every redirect agrees.

    The authenticated request stops at Moodle's first response, so cookies are
    never sent to the external meeting provider. Any missing, unsafe or
    conflicting Location keeps the original ambiguity.
    """
    if not 2 <= len(candidates) <= 8 or not hasattr(context, "request"):
        return None
    targets = set()
    for candidate in candidates:
        parsed = urlsplit(candidate.url)
        if parsed.hostname != "lms.ranepa.ru" or parsed.path != "/mod/url/view.php":
            return None
        response = None
        try:
            response = await context.request.get(
                candidate.url,
                max_redirects=0,
                timeout=8000,
                fail_on_status_code=False,
            )
            location = response.headers.get("location")
            target = meeting_url(urljoin(candidate.url, location or ""))
            if target is None or urlsplit(target).hostname == "lms.ranepa.ru":
                return None
            targets.add(target)
        except Exception:  # noqa: BLE001 -- optional read-only deduplication
            return None
        finally:
            if response is not None:
                await response.dispose()
    return next(iter(targets)) if len(targets) == 1 else None


async def _choose_course_meeting(context, candidates, lesson, *, first_week_anchor=None):
    choice = select_lesson_link(
        candidates,
        teacher=lesson.teacher or "",
        group_label=lesson.subgroup_hint or "",
        day=lesson.day,
        starts=lesson.starts,
        lesson_type=lesson.lesson_type,
        first_week_anchor=first_week_anchor,
    )
    if choice.url is None and choice.may_resolve_duplicates:
        shared_target = await _shared_moodle_redirect(context, choice.candidates)
        if shared_target is not None:
            choice = LinkChoice(LinkState.MATCHED, shared_target, choice.candidates)
    return choice


async def _load_moodle_dashboard(context, login, password):
    """Authenticate to Moodle separately; do not treat an LK session as LMS login."""
    page = await context.new_page()
    try:
        _login_stage("lms_dashboard_requested")
        await page.goto("https://lms.ranepa.ru/my/", wait_until="domcontentloaded", timeout=30000)
        login_attempted = False
        courses = {}
        for _ in range(40):
            parsed = urlsplit(page.url)
            if parsed.scheme != "https" or parsed.hostname != "lms.ranepa.ru":
                return {}, LinkState.UNEXPECTED_REDIRECT
            if parsed.path == "/login/index.php":
                if login_attempted:
                    await page.wait_for_timeout(250)
                    continue
                login_attempted = True
                _login_stage("lms_login_form")
                username = page.locator('input[name="username"]')
                secret = page.locator('input[name="password"]')
                if await username.count() != 1 or await secret.count() != 1:
                    return {}, LinkState.LMS_LOGIN_REQUIRED
                await username.fill(login, timeout=8000)
                await secret.fill(password, timeout=8000)
                await page.locator("#loginbtn").click(timeout=8000)
                _login_stage("lms_login_submitted")
                continue
            if parsed.path in {"/my/", "/my/index.php", "/my/courses.php", "/"}:
                courses.update(dashboard_courses(await page.content()))
                if courses:
                    break
            await page.wait_for_timeout(250)
        if urlsplit(page.url).path == "/login/index.php":
            return {}, LinkState.LMS_LOGIN_REQUIRED
        # Moodle dashboard may show only recent courses; the enrolled-course
        # list is the authoritative supplementary read.
        await page.goto(
            "https://lms.ranepa.ru/my/courses.php",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        for _ in range(12):
            parsed = urlsplit(page.url)
            if parsed.hostname != "lms.ranepa.ru" or parsed.path != "/my/courses.php":
                break
            courses.update(dashboard_courses(await page.content()))
            await page.wait_for_timeout(250)
        if courses:
            _login_stage("lms_courses_loaded")
        return courses, None if courses else LinkState.COURSE_CONTENT_UNAVAILABLE
    except Exception:  # noqa: BLE001 -- no private page details in diagnostics
        return {}, LinkState.NAVIGATION_ERROR
    finally:
        await page.close()


async def resolve_moodle_courses(
    context,
    result,
    login,
    password,
    *,
    dashboard_cache,
    course_cache,
    first_week_anchor=None,
    deadline=None,
):
    """Find course by timetable subject; reuse inventories within this account only."""
    linked, states, courses, inventories = {}, {}, {}, {}
    if result.snapshot is None:
        return SdoLinkResolution(linked, states, courses, inventories)
    if not any(lesson.room == "СДО" for lesson in result.snapshot.lessons):
        return SdoLinkResolution(linked, states, courses, inventories)
    if "courses" not in dashboard_cache:
        try:
            remaining = None if deadline is None else max(0.01, deadline - monotonic())
            async with asyncio.timeout(remaining):
                index, error = await _load_moodle_dashboard(context, login, password)
        except TimeoutError:
            index, error = {}, LinkState.NAVIGATION_ERROR
        # One independent Moodle login per account. In particular, do not
        # resubmit a rejected form for each following week.
        dashboard_cache.update(courses=index, error=error)
    for lesson in result.snapshot.lessons:
        if lesson.room != "СДО":
            continue
        key = (
            lesson.day.isoformat(),
            lesson.starts.isoformat(),
            lesson.ends.isoformat(),
            lesson.subject,
            lesson.teacher,
            lesson.room,
        )
        error = dashboard_cache["error"]
        if error is not None:
            states[key] = error
            continue
        if deadline is not None and monotonic() >= deadline:
            break
        matches = matching_courses(dashboard_cache["courses"], lesson.subject)
        if len(matches) != 1:
            states[key] = LinkState.AMBIGUOUS if matches else LinkState.NOT_FOUND
            continue
        course = matches[0]
        courses[key] = course
        if course not in course_cache:
            page = await context.new_page()
            try:
                _login_stage("lms_course_requested")
                await page.goto(course, wait_until="domcontentloaded", timeout=15000)
                parsed = urlsplit(page.url)
                if parsed.hostname != "lms.ranepa.ru" or parsed.path != "/course/view.php":
                    states[key] = LinkState.COURSE_CONTENT_UNAVAILABLE
                    continue
                if parse_qs(parsed.query).get("id") != parse_qs(urlsplit(course).query).get("id"):
                    states[key] = LinkState.IDENTITY_MISSING
                    continue
                remaining = None if deadline is None else max(0.01, deadline - monotonic())
                async with asyncio.timeout(remaining):
                    course_cache[course] = await _read_course_candidates(page, context)
            except Exception:  # noqa: BLE001
                states[key] = LinkState.NAVIGATION_ERROR
                continue
            finally:
                await page.close()
        candidates, verified = course_cache[course]
        if not verified or not candidates:
            states[key] = (
                LinkState.NO_COURSE_LINKS if verified else LinkState.COURSE_CONTENT_UNAVAILABLE
            )
            continue
        inventories[key] = candidates
        try:
            remaining = None if deadline is None else max(0.01, deadline - monotonic())
            async with asyncio.timeout(remaining):
                choice = await _choose_course_meeting(
                    context, candidates, lesson, first_week_anchor=first_week_anchor
                )
        except TimeoutError:
            states[key] = LinkState.NOT_CHECKED
            break
        states[key] = choice.state
        if choice.url is not None:
            linked[key] = choice.url
    return SdoLinkResolution(linked, states, courses, inventories)


async def resolve_sdo_exits(
    context,
    result: BrowserResult,
    login: str = "",
    password: str = "",
    *,
    solve_captcha=None,
    on_captcha=None,
    course_cache: dict | None = None,
    exit_cache: dict | None = None,
    dashboard_cache: dict | None = None,
    first_week_anchor: date | None = None,
    deadline=None,
) -> SdoLinkResolution:
    """Read Moodle independently when dashboard mode is enabled."""
    if dashboard_cache is not None:
        return await resolve_moodle_courses(
            context,
            result,
            login,
            password,
            dashboard_cache=dashboard_cache,
            course_cache=course_cache if course_cache is not None else {},
            first_week_anchor=first_week_anchor,
            deadline=deadline,
        )
    if result.snapshot is None or len(result.sdo_exits) != len(result.snapshot.lessons):
        return SdoLinkResolution({}, {"not_checked": 1}, {}, {})
    linked = {}
    states = {}
    courses = {}
    candidates_by_lesson = {}
    if course_cache is None:
        course_cache = {}
    if exit_cache is None:
        exit_cache = {}
    for lesson, exit_url in zip(result.snapshot.lessons, result.sdo_exits, strict=True):
        if lesson.room != "СДО":
            continue
        key = (
            lesson.day.isoformat(),
            lesson.starts.isoformat(),
            lesson.ends.isoformat(),
            lesson.subject,
            lesson.teacher,
            lesson.room,
        )
        if exit_url is None:
            states[key] = LinkState.MISSING_COURSE_TRANSITION
            continue
        cached_course = exit_cache.get(exit_url)
        if cached_course is not None:
            candidates, verified = course_cache.get(cached_course, ((), False))
            if verified and candidates:
                try:
                    choice = await _choose_course_meeting(context, candidates, lesson)
                except Exception:  # noqa: BLE001 -- use the normal verified read below
                    LOGGER.debug("cached_sdo_choice_unavailable")
                else:
                    courses[key] = cached_course
                    candidates_by_lesson[key] = candidates
                    states[key] = choice.state
                    if choice.url is not None:
                        linked[key] = choice.url
                    continue
        page = await context.new_page()
        try:
            await page.goto(exit_url, wait_until="domcontentloaded", timeout=30000)
            choice = None
            lms_login_attempted = False
            retried_exit_after_login = False
            empty_course_reads = 0
            captcha_seen = False
            for _ in range(80):
                # Observe the real challenge even if paid solving is
                # disabled; an unresolved SDO exit must be counted by the
                # queue circuit breaker instead of looking like success.
                if await page.get_by_text("Используйте ползунки", exact=False).is_visible():
                    captcha_seen = True
                    if on_captcha is not None:
                        await on_captcha("sdo")
                    if solve_captcha is not None and await solve_captcha(page):
                        captcha_seen = False
                        continue
                    break
                parsed = urlsplit(page.url)
                if (
                    parsed.hostname == "lms.ranepa.ru"
                    and parsed.path in {"/my/", "/"}
                    and lms_login_attempted
                    and not retried_exit_after_login
                ):
                    retried_exit_after_login = True
                    await page.goto(exit_url, wait_until="domcontentloaded", timeout=30000)
                    continue
                if (
                    parsed.hostname == "lms.ranepa.ru"
                    and parsed.path == "/login/index.php"
                    and not lms_login_attempted
                ):
                    lms_login_attempted = True
                    if not login or not password:
                        break
                    username_field = page.locator('input[name="username"]')
                    password_field = page.locator('input[name="password"]')
                    if await username_field.count() != 1 or await password_field.count() != 1:
                        break
                    await username_field.fill(login)
                    await password_field.fill(password)
                    await page.locator("#loginbtn").click(timeout=8000)
                    continue
                if (
                    parsed.hostname == "lms.ranepa.ru"
                    and parsed.path == "/login/index.php"
                    and lms_login_attempted
                ):
                    break
                if parsed.hostname == "lms.ranepa.ru" and parsed.path == "/course/view.php":
                    try:
                        ids = parse_qs(parsed.query).get("id", [])
                        if len(ids) != 1 or not ids[0].isdigit():
                            break
                        course = f"https://lms.ranepa.ru/course/view.php?id={ids[0]}"
                        courses[key] = course
                        if course not in course_cache:
                            course_cache[course] = await _read_course_candidates(page, context)
                        candidates, verified = course_cache[course]
                        if not verified:
                            empty_course_reads += 1
                            if empty_course_reads >= 8:
                                break
                            await page.wait_for_timeout(250)
                            course_cache.pop(course, None)
                            continue
                        if not candidates:
                            # An empty but verified shell is not proof the
                            # course will stay empty on a later week.
                            course_cache.pop(course, None)
                            empty_course_reads = 8
                            break
                        candidates_by_lesson[key] = candidates
                        exit_cache[exit_url] = course
                        choice = await _choose_course_meeting(context, candidates, lesson)
                        break
                    except SourceAccessError:
                        pass
                await page.wait_for_timeout(250)
            if choice is None:
                final = urlsplit(page.url)
                if captcha_seen:
                    states[key] = LinkState.CAPTCHA_REQUIRED
                elif final.hostname == "lms.ranepa.ru" and final.path == "/login/index.php":
                    states[key] = LinkState.LMS_LOGIN_REQUIRED
                elif final.hostname == "lms.ranepa.ru" and final.path == "/course/view.php":
                    states[key] = (
                        LinkState.NO_COURSE_LINKS
                        if empty_course_reads
                        else LinkState.COURSE_CONTENT_UNAVAILABLE
                    )
                elif final.hostname == "lms.ranepa.ru":
                    states[key] = LinkState.LMS_OTHER_PAGE
                elif final.hostname == HOST:
                    states[key] = LinkState.CABINET_TRANSITION_UNAVAILABLE
                else:
                    states[key] = LinkState.UNEXPECTED_REDIRECT
            else:
                states[key] = choice.state
                if choice.url is not None:
                    linked[key] = choice.url
        except Exception:  # noqa: BLE001
            states[key] = LinkState.NAVIGATION_ERROR
        finally:
            await page.close()
    return SdoLinkResolution(linked, states, courses, candidates_by_lesson)
