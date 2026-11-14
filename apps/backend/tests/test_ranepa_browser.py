"""The month browser renews a session that expires during navigation."""

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from studgroup import ranepa_browser
from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot


def test_open_reuses_rendered_schedule_page(monkeypatch):
    calls = []

    class Page:
        url = "https://my.ranepa.ru/lk/student/schedule"

        async def goto(self, *_args, **_kwargs):
            calls.append("goto")

    async def rendered(_page):
        return "rendered"

    monkeypatch.setattr(ranepa_browser, "_wait_visible_state", rendered)
    assert asyncio.run(ranepa_browser._open(Page(), "login", "password")) == "rendered"
    assert calls == []


def test_stormwall_js_redirect_waits_until_real_page_is_rendered():
    async def scenario():
        class Locator:
            def __init__(self, page, selector):
                self.page, self.selector = page, selector

            async def inner_text(self, **kwargs):
                return "Проверка браузера" if self.page.spinner else "Расписание"

            async def count(self):
                return 0

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            spinner = True
            waited = 0

            def locator(self, selector):
                return Locator(self, selector)

            async def content(self):
                if self.spinner:
                    return "<script>get_jhash('__js_p_')</script>"
                return "<main>Расписание</main>"

            async def title(self):
                return "Проверяем браузер" if self.spinner else "Расписание"

            async def wait_for_timeout(self, milliseconds):
                assert milliseconds == 250
                self.waited += 1
                self.spinner = False

        page = Page()
        assert await ranepa_browser._state(page) == "challenge_pending"
        assert await ranepa_browser._wait_visible_state(page) == "rendered"
        assert page.waited == 1

    asyncio.run(scenario())


def test_captcha_during_calendar_navigation_is_not_a_parse_failure(monkeypatch):
    async def scenario():
        class Clicker:
            async def click(self, **kwargs):
                return None

        class Page:
            def get_by_role(self, *args, **kwargs):
                return Clicker()

            def locator(self, *args, **kwargs):
                return self

            def nth(self, index):
                return Clicker()

            async def content(self):
                raise AssertionError("CAPTCHA must not be parsed as a calendar")

        async def open_(*args):
            return "rendered"

        async def verified(*args):
            return True

        async def captcha(*args):
            return "captcha_required"

        monkeypatch.setattr(ranepa_browser, "_open", open_)
        monkeypatch.setattr(ranepa_browser, "_verify_group", verified)
        monkeypatch.setattr(ranepa_browser, "_state", captcha)
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        page = Page()
        month = await ranepa_browser.read_month(
            page, "student", "secret", "A-001", today.year, today.month
        )
        assert month.status == "captcha_required"
        monday = today - timedelta(days=today.weekday())
        week = await ranepa_browser.read_week(page, "student", "secret", "A-001", monday)
        assert week.status == "captcha_required"

    asyncio.run(scenario())


def test_month_relogs_in_after_session_expires_mid_navigation(monkeypatch):
    calls = {"open": 0, "state": 0}

    class Clicker:
        async def click(self):
            return None

    class Page:
        def get_by_role(self, *args, **kwargs):
            return Clicker()

        def locator(self, *args, **kwargs):
            return self

        def nth(self, index):
            return Clicker()

        async def content(self):
            return "checked page"

        async def wait_for_timeout(self, milliseconds):
            return None

    async def open_(page, login, password):
        calls["open"] += 1
        return "rendered"

    async def verify(page, code):
        return True

    async def state(page):
        calls["state"] += 1
        return "login_required" if calls["state"] <= 2 else "rendered"

    monkeypatch.setattr(ranepa_browser, "_open", open_)
    monkeypatch.setattr(ranepa_browser, "_verify_group", verify)
    monkeypatch.setattr(ranepa_browser, "_state", state)
    monkeypatch.setattr(ranepa_browser, "extract_cabinet_month", lambda *args: ("card",))
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    result = asyncio.run(read_month_for_current_month(Page(), today))
    assert result.status == "preview_only" and result.cards == ("card",)
    assert calls["open"] == 2


def test_week_waits_for_cards_after_a_transient_empty_grid(monkeypatch):
    class Clicker:
        async def click(self):
            return None

    class Page:
        def get_by_role(self, *args, **kwargs):
            return Clicker()

        def locator(self, *args, **kwargs):
            return self

        def nth(self, index):
            return Clicker()

        async def content(self):
            return "grid"

        async def wait_for_timeout(self, milliseconds):
            return None

    async def rendered(*args):
        return "rendered"

    async def verified(*args):
        return True

    calls = 0
    monday = date(2026, 11, 9)
    snapshot = WeekSnapshot("Э-ОБ-385-04-26-02", monday, date(2026, 11, 15), ())

    def parse(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SourceAccessError("empty_source_requires_review")
        return snapshot

    async def enrich(page, value):
        return ranepa_browser.BrowserResult("preview_only", value)

    monkeypatch.setattr(ranepa_browser, "_open", rendered)
    monkeypatch.setattr(ranepa_browser, "_verify_group", verified)
    monkeypatch.setattr(ranepa_browser, "_state", rendered)
    monkeypatch.setattr(ranepa_browser, "extract_cabinet_week", parse)
    monkeypatch.setattr(ranepa_browser, "_enrich", enrich)
    result = asyncio.run(
        ranepa_browser.read_week(Page(), "login", "password", snapshot.group_code, monday)
    )
    assert result.status == "preview_only" and calls == 2


async def read_month_for_current_month(page, today):
    return await ranepa_browser.read_month(
        page, "login", "password", "Э-ОБ-385-04-26-02", today.year, today.month
    )


def test_sdo_transition_logs_in_to_moodle_and_keeps_stable_url():
    class Field:
        def __init__(self, page, name):
            self.page, self.name = page, name

        async def count(self):
            return 1

        async def fill(self, value):
            self.page.filled[self.name] = value

        async def click(self, **kwargs):
            self.page.url = "https://lms.ranepa.ru/course/view.php?id=85813"

    class Page:
        def __init__(self):
            self.url = ""
            self.filled = {}

        async def goto(self, url, **kwargs):
            self.url = "https://lms.ranepa.ru/login/index.php"

        def locator(self, selector):
            return Field(self, selector)

        async def content(self):
            return (
                '<main class="course-content"><a href="/course/view.php?id=85813">Курс</a>'
                '<a href="/mod/url/view.php?id=1425553&sesskey=private">'
                "Клишина О. С._Э-ОБ26-СГЯ-лек(43,44,45)_постоянная ссылка</a></main>"
            )

        async def close(self):
            return None

        async def wait_for_timeout(self, milliseconds):
            return None

    class Context:
        def __init__(self):
            self.page = Page()

        async def new_page(self):
            return self.page

    lesson = ExternalLesson(
        day=date(2026, 11, 16),
        starts=time(9),
        ends=time(10, 20),
        subject="Культура личности и общества",
        teacher="Клишина Ольга Семеновна",
        room="СДО",
        subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45)",
    )
    result = ranepa_browser.BrowserResult(
        "preview_only",
        WeekSnapshot("Э-ОБ-385-04-26-02", date(2026, 11, 16), date(2026, 11, 22), (lesson,)),
        ("https://my.ranepa.ru/moodle_out.php?id=1",),
    )
    context = Context()
    links, states, courses = asyncio.run(
        ranepa_browser.resolve_sdo_exits(context, result, "login", "password")
    )
    assert list(states.values()) == ["matched"]
    assert list(links.values()) == ["https://lms.ranepa.ru/mod/url/view.php?id=1425553"]
    assert list(courses.values()) == ["https://lms.ranepa.ru/course/view.php?id=85813"]
    assert context.page.filled == {
        'input[name="username"]': "login",
        'input[name="password"]': "password",
    }

    async def direct_course(url, **kwargs):
        context.page.url = "https://lms.ranepa.ru/course/view.php?id=85813"

    async def empty_course():
        return '<main class="course-content"><a href="/course/view.php?id=85813">Курс</a></main>'

    context.page.goto = direct_course
    context.page.content = empty_course
    links, states, courses = asyncio.run(ranepa_browser.resolve_sdo_exits(context, result))
    assert links == {} and list(states.values()) == ["no_course_links"]
    assert list(courses.values()) == ["https://lms.ranepa.ru/course/view.php?id=85813"]


def test_wait_for_captcha_resolution_observes_delayed_change_without_new_actions(monkeypatch):
    async def scenario():
        states = iter(["captcha_required", "challenge_pending", "rendered"])
        observed = []

        async def page_state(_page):
            observed.append("read")
            return next(states)

        class Page:
            async def wait_for_timeout(self, milliseconds):
                assert milliseconds == 250
                observed.append("wait")

        monkeypatch.setattr(ranepa_browser, "_state", page_state)
        result = await ranepa_browser.wait_for_captcha_resolution(Page(), timeout_seconds=1)
        assert result == "challenge_page_changed_unverified"
        assert observed == ["read", "wait", "read", "wait", "read"]

    asyncio.run(scenario())


def test_wait_for_captcha_resolution_times_out_without_resubmission(monkeypatch):
    async def scenario():
        observed = []

        async def always_captcha(_page):
            observed.append("read")
            return "captcha_required"

        class Page:
            async def wait_for_timeout(self, milliseconds):
                assert milliseconds == 250
                observed.append("wait")
                await asyncio.sleep(0.02)

        monkeypatch.setattr(ranepa_browser, "_state", always_captcha)
        result = await ranepa_browser.wait_for_captcha_resolution(Page(), timeout_seconds=0.035)
        assert result == "challenge_still_visible_after_wait"
        assert observed.count("read") >= 1
        assert observed.count("wait") >= 1

    asyncio.run(scenario())


def test_wait_for_captcha_resolution_distinguishes_redirect_and_login(monkeypatch):
    async def scenario():
        async def verify(_page):
            return "login_required"

        class Page:
            async def wait_for_timeout(self, _milliseconds):
                raise AssertionError("must return as soon as state changes")

        monkeypatch.setattr(ranepa_browser, "_state", verify)
        assert (
            await ranepa_browser.wait_for_captcha_resolution(Page())
            == "challenge_login_visible_after_drag"
        )

        async def redirected(_page):
            return "unexpected_redirect"

        monkeypatch.setattr(ranepa_browser, "_state", redirected)
        assert (
            await ranepa_browser.wait_for_captcha_resolution(Page())
            == "challenge_redirect_after_drag"
        )

    asyncio.run(scenario())


def test_wait_for_captcha_resolution_does_not_expose_page_errors(monkeypatch):
    async def scenario():
        async def broken(_page):
            raise ValueError("private session token")

        monkeypatch.setattr(ranepa_browser, "_state", broken)
        assert (
            await ranepa_browser.wait_for_captcha_resolution(object())
            == "challenge_state_unavailable"
        )

    asyncio.run(scenario())
