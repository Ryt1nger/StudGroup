"""Read-only personal-cabinet browser port with one automatic re-login."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlsplit
from zoneinfo import ZoneInfo

from studgroup.ranepa_cabinet import MonthCard, extract_cabinet_month, extract_cabinet_week
from studgroup.ranepa_lms import course_links, select_lesson_link
from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot, group_code
from studgroup.ranepa_ui import page_status

SCHEDULE = "https://my.ranepa.ru/lk/student/schedule"
HOST = "my.ranepa.ru"


@dataclass(frozen=True)
class BrowserResult:
    status: str
    snapshot: WeekSnapshot | None = None
    sdo_exits: tuple[str | None, ...] = ()


@dataclass(frozen=True)
class BrowserMonthResult:
    status: str
    cards: tuple[MonthCard, ...] = ()


async def _state(page) -> str:
    if urlsplit(page.url).hostname != HOST:
        return "unexpected_redirect"
    body = await page.locator("body").inner_text(timeout=8000)
    return page_status(
        page.url, body, password_input=await page.locator('input[type="password"]').count() > 0
    )


async def _wait_visible_state(page) -> str:
    state = "empty_page"
    for _ in range(100):
        state = await _state(page)
        if state != "empty_page":
            break
        await page.wait_for_timeout(250)
    return state


async def _sign_in(page, login: str, password: str) -> str:
    await page.get_by_placeholder("Логин").fill(login, timeout=8000)
    await page.locator('input[type="password"]').fill(password, timeout=8000)
    await page.get_by_role("button", name="Войти", exact=True).click(timeout=8000)
    for _ in range(100):
        state = await _state(page)
        if state not in {"login_required", "empty_page"}:
            return state
        await page.wait_for_timeout(250)
    return "login_failed"


async def _open(page, login: str, password: str) -> str:
    await page.goto(SCHEDULE, wait_until="domcontentloaded", timeout=30000)
    state = await _wait_visible_state(page)
    if state == "login_required":
        state = await _sign_in(page, login, password)
        if state == "rendered":
            await page.goto(SCHEDULE, wait_until="domcontentloaded", timeout=30000)
            state = await _wait_visible_state(page)
    return state


async def _verify_group(page, code: str) -> bool:
    avatar = page.locator(".mobile-header__avatar")
    await avatar.click(timeout=8000)
    active = page.locator(".avatar-navigation__role-btn.active")
    labels = await active.all_text_contents()
    await avatar.click(timeout=8000)
    expected = group_code(code).casefold()
    return len(labels) == 1 and labels[0].strip().casefold().startswith(expected + " ")


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
                return BrowserResult(state)
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
            for _ in range(40):
                if await _state(page) == "login_required":
                    break
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
                        "empty_source_requires_review",
                    }:
                        return BrowserResult(str(error))
                await page.wait_for_timeout(250)
            if await _state(page) != "login_required":
                return BrowserResult("cabinet_week_incomplete")
        except Exception:  # noqa: BLE001
            # Playwright can raise rich network exceptions containing URLs or
            # server text; never let them enter a job error or log.
            return BrowserResult("source_unavailable")
        if attempt == 1:
            break
    return BrowserResult("login_failed")


async def read_month(
    page, login: str, password: str, code: str, year: int, month: int
) -> BrowserMonthResult:
    """Read month index through the same authenticated interface, without DB writes."""
    for attempt in range(2):
        try:
            state = await _open(page, login, password)
            if state != "rendered":
                return BrowserMonthResult(state)
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
                if await _state(page) == "login_required":
                    break
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
            if await _state(page) != "login_required":
                return BrowserMonthResult("cabinet_month_incomplete")
        except Exception:  # noqa: BLE001
            return BrowserMonthResult("source_unavailable")
        if attempt == 1:
            break
    return BrowserMonthResult("login_failed")


async def resolve_sdo_exits(
    context, result: BrowserResult, login: str = "", password: str = ""
) -> tuple[dict, dict, dict]:
    """Follow LK SDO exits; retain verified course and unique meeting URLs."""
    if result.snapshot is None or len(result.sdo_exits) != len(result.snapshot.lessons):
        return {}, {"not_checked": 1}, {}
    linked = {}
    states = {}
    courses = {}
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
            states[key] = "missing_course_transition"
            continue
        page = await context.new_page()
        try:
            await page.goto(exit_url, wait_until="domcontentloaded", timeout=30000)
            choice = None
            lms_login_attempted = False
            retried_exit_after_login = False
            empty_course_reads = 0
            for _ in range(80):
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
                        candidates = course_links(await page.content())
                        ids = parse_qs(parsed.query).get("id", [])
                        if len(ids) == 1 and ids[0].isdigit():
                            courses[key] = f"https://lms.ranepa.ru/course/view.php?id={ids[0]}"
                        if not candidates:
                            empty_course_reads += 1
                            if empty_course_reads >= 8:
                                break
                            await page.wait_for_timeout(250)
                            continue
                        choice = select_lesson_link(
                            candidates,
                            teacher=lesson.teacher or "",
                            group_label=lesson.subgroup_hint or "",
                            day=lesson.day,
                            starts=lesson.starts,
                        )
                        break
                    except SourceAccessError:
                        pass
                await page.wait_for_timeout(250)
            if choice is None:
                final = urlsplit(page.url)
                if final.hostname == "lms.ranepa.ru" and final.path == "/login/index.php":
                    states[key] = "lms_login_required"
                elif final.hostname == "lms.ranepa.ru" and final.path == "/course/view.php":
                    states[key] = (
                        "no_course_links" if empty_course_reads else "course_content_unavailable"
                    )
                elif final.hostname == "lms.ranepa.ru":
                    states[key] = "lms_other_page"
                elif final.hostname == HOST:
                    states[key] = "cabinet_transition_unavailable"
                else:
                    states[key] = "unexpected_redirect"
            else:
                states[key] = choice.state
                if choice.url is not None:
                    linked[key] = choice.url
        except Exception:  # noqa: BLE001
            states[key] = "navigation_error"
        finally:
            await page.close()
    return linked, states, courses
