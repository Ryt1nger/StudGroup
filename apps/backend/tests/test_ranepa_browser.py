"""The month browser renews a session that expires during navigation."""

import asyncio
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

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


@pytest.mark.parametrize(
    "first_error",
    ["empty_source_requires_review", "cabinet_day_mismatch", "cabinet_weekday_mismatch"],
)
def test_week_waits_for_cards_after_a_transient_empty_grid(monkeypatch, first_error):
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
            raise SourceAccessError(first_error)
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

        def get_by_text(self, text, **kwargs):
            class Invisible:
                async def is_visible(self):
                    return False

            return Invisible()

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


def test_sdo_exit_records_captcha_even_without_paid_solver():
    class Visible:
        async def is_visible(self):
            return True

    class Page:
        url = "https://my.ranepa.ru/moodle_out.php"

        async def goto(self, url, **kwargs):
            self.url = url

        def get_by_text(self, label, **kwargs):
            assert "Используйте ползунки" in label
            return Visible()

        async def close(self):
            return None

    class Context:
        async def new_page(self):
            return Page()

    lesson = ExternalLesson(
        day=date(2026, 11, 16),
        starts=time(9),
        ends=time(10, 20),
        subject="Test course",
        teacher="Test teacher",
        room="СДО",
    )
    result = ranepa_browser.BrowserResult(
        "preview_only",
        WeekSnapshot("A-001", date(2026, 11, 16), date(2026, 11, 22), (lesson,)),
        ("https://my.ranepa.ru/moodle_out.php?id=1",),
    )
    noticed = []

    async def on_captcha(location):
        noticed.append(location)

    links, states, courses = asyncio.run(
        ranepa_browser.resolve_sdo_exits(Context(), result, on_captcha=on_captcha)
    )
    assert noticed == ["sdo"]
    assert links == {}
    assert courses == {}
    assert list(states.values()) == ["captcha_required"]


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


def test_sdo_scans_hidden_course_page_and_preserves_all_teacher_candidates():
    class Invisible:
        async def is_visible(self):
            return False

    class FakePage:
        def __init__(self):
            self.url = ""

        async def goto(self, url, **_kwargs):
            self.url = (
                "https://lms.ranepa.ru/course/view.php?id=85813" if "moodle_out.php" in url else url
            )

        def get_by_text(self, *_args, **_kwargs):
            return Invisible()

        async def evaluate(self, _script):
            return None

        async def content(self):
            if "/mod/page/view.php" in self.url:
                return (
                    "<main><p>Петров П. П. Э-ОБ26-СГЯ-лек(43,44,45) "
                    "постоянная ссылка https://my.mts-link.ru/j/Ranepa/222"
                    "</p></main>"
                )
            return (
                '<main class="course-content">'
                '<a href="/course/view.php?id=85813">Курс</a>'
                "<details><summary>Иванов И. И. Э-ОБ26-СГЯ-лек(43,44,45) "
                "постоянная ссылка</summary>"
                '<p><a href="/mod/url/view.php?id=111">Присоединиться</a></p></details>'
                '<a href="/mod/page/view.php?id=77&sesskey=private">Материалы</a>'
                "</main>"
            )

        async def wait_for_timeout(self, _milliseconds):
            pass

        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return FakePage()

    lesson = ExternalLesson(
        day=date(2026, 11, 19),
        starts=time(9),
        ends=time(10, 20),
        subject="История России",
        teacher="Петров Павел Петрович",
        room="СДО",
        subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45)",
    )
    snapshot = WeekSnapshot(
        "Э-ОБ-385-04-26-02",
        date(2026, 11, 16),
        date(2026, 11, 22),
        (lesson,),
    )
    result = asyncio.run(
        ranepa_browser.resolve_sdo_exits(
            Context(),
            ranepa_browser.BrowserResult(
                "preview_only",
                snapshot,
                ("https://my.ranepa.ru/moodle_out.php?id=1",),
            ),
        )
    )
    links, states, courses = result
    assert list(states.values()) == ["matched"]
    assert list(links.values()) == ["https://my.mts-link.ru/j/Ranepa/222"]
    assert list(courses.values()) == ["https://lms.ranepa.ru/course/view.php?id=85813"]
    inventory = next(iter(result.candidates.values()))
    assert len(inventory) == 2
    assert {item.url for item in inventory} == {
        "https://lms.ranepa.ru/mod/url/view.php?id=111",
        "https://my.mts-link.ru/j/Ranepa/222",
    }


def test_duplicate_moodle_activities_with_one_safe_target_are_one_meeting():
    class Invisible:
        async def is_visible(self):
            return False

    class Response:
        def __init__(self):
            self.headers = {"location": "https://my.mts-link.ru/j/Ranepa/777"}

        async def dispose(self):
            pass

    class Request:
        def __init__(self):
            self.calls = []

        async def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    class Page:
        def __init__(self):
            self.url = ""

        async def goto(self, url, **_kwargs):
            self.url = "https://lms.ranepa.ru/course/view.php?id=85813"

        def get_by_text(self, *_args, **_kwargs):
            return Invisible()

        async def evaluate(self, _script):
            pass

        async def content(self):
            return (
                '<main class="course-content"><a href="/course/view.php?id=85813">Курс</a>'
                '<a href="/mod/url/view.php?id=101">Федотова Е.А. '
                "Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ Гиперссылка</a>"
                '<a href="/mod/url/view.php?id=102">Федотова Е.А. '
                "Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ Гиперссылка</a></main>"
            )

        async def wait_for_timeout(self, _milliseconds):
            pass

        async def close(self):
            pass

    class Context:
        def __init__(self):
            self.request = Request()

        async def new_page(self):
            return Page()

    context = Context()
    lesson = ExternalLesson(
        day=date(2026, 11, 23),
        starts=time(9),
        ends=time(10, 20),
        subject="История России",
        teacher="Федотова Елена Алексеевна",
        room="СДО",
        subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ",
    )
    result = asyncio.run(
        ranepa_browser.resolve_sdo_exits(
            context,
            ranepa_browser.BrowserResult(
                "preview_only",
                WeekSnapshot(
                    "Э-ОБ-385-04-26-02",
                    date(2026, 11, 23),
                    date(2026, 11, 29),
                    (lesson,),
                ),
                ("https://my.ranepa.ru/moodle_out.php?id=1",),
            ),
        )
    )

    assert list(result.states.values()) == ["matched"]
    assert list(result.links.values()) == ["https://my.mts-link.ru/j/Ranepa/777"]
    assert len(context.request.calls) == 2
    assert all(call[1]["max_redirects"] == 0 for call in context.request.calls)


def test_duplicate_moodle_activities_with_different_targets_stay_unverified():
    from studgroup.ranepa_browser import _shared_moodle_redirect
    from studgroup.ranepa_lms import LinkCandidate

    class Response:
        def __init__(self, location):
            self.headers = {"location": location}

        async def dispose(self):
            pass

    class Request:
        async def get(self, url, **_kwargs):
            ident = url.rsplit("=", 1)[1]
            return Response(f"https://my.mts-link.ru/j/Ranepa/{ident}")

    class Context:
        request = Request()

    candidates = tuple(
        LinkCandidate(f"https://lms.ranepa.ru/mod/url/view.php?id={ident}", "label")
        for ident in (101, 102)
    )
    assert asyncio.run(_shared_moodle_redirect(Context(), candidates)) is None


def test_course_reader_waits_for_late_meetings_from_other_teachers():
    class Page:
        def __init__(self):
            self.reads = 0
            self.waits = 0

        async def content(self):
            self.reads += 1
            links = ""
            if self.reads >= 5:
                links = (
                    '<a href="/mod/url/view.php?id=100">'
                    "Иванов И.И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка</a>"
                    '<a href="/mod/url/view.php?id=101">'
                    "Петров П.П. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка</a>"
                )
            return (
                '<main class="course-content">'
                '<a href="/course/view.php?id=85813">Курс</a>' + links + "</main>"
            )

        async def wait_for_timeout(self, milliseconds):
            assert milliseconds == 250
            self.waits += 1

        async def evaluate(self, _script):
            pass

    class Context:
        async def new_page(self):
            raise AssertionError("No nested page was offered")

    page = Page()
    candidates, verified = asyncio.run(ranepa_browser._read_course_candidates(page, Context()))
    assert verified is True
    assert len(candidates) == 2
    assert page.reads >= 5
    assert page.waits >= 4
    assert {item.url for item in candidates} == {
        "https://lms.ranepa.ru/mod/url/view.php?id=100",
        "https://lms.ranepa.ru/mod/url/view.php?id=101",
    }


def test_sign_in_timeout_does_not_claim_invalid_password(monkeypatch):
    class Page:
        def get_by_placeholder(self, *args):
            return self

        def locator(self, *args):
            return self

        def get_by_role(self, *args, **kwargs):
            return self

        async def fill(self, *args, **kwargs):
            pass

        async def click(self, **kwargs):
            pass

        async def wait_for_timeout(self, *args):
            pass

    async def still_waiting(*args):
        return "login_required"

    from studgroup.ranepa_crawl_trace import CrawlTrace

    monkeypatch.setattr(ranepa_browser, "_state", still_waiting)
    trace = CrawlTrace()
    token = ranepa_browser.browser_trace.set(trace)
    try:
        assert asyncio.run(ranepa_browser._sign_in(Page(), "student", "test")) == "timeout"
    finally:
        ranepa_browser.browser_trace.reset(token)
    assert trace.last_notification_step() == "lk_form_still_visible"
    assert [event["step"] for event in trace.events] == [
        "lk_form_visible",
        "lk_login_filled",
        "lk_password_filled",
        "lk_submit_requested",
        "lk_form_submitted",
        "lk_response_recheck",
        "lk_response_recheck",
        "lk_form_still_visible",
    ]

    async def rejected(*args):
        return "login_failed"

    monkeypatch.setattr(ranepa_browser, "_state", rejected)
    assert asyncio.run(ranepa_browser._sign_in(Page(), "student", "test")) == "login_failed"


@pytest.mark.parametrize(("failures", "expected_calls"), [(2, 3), (3, 3)])
def test_schedule_connection_rechecks_are_bounded(monkeypatch, failures, expected_calls):
    class Page:
        calls = 0
        url = "about:blank"

        async def goto(self, *args, **kwargs):
            self.calls += 1
            if self.calls <= failures:
                raise ConnectionError("test connection failure")

        async def wait_for_timeout(self, milliseconds):
            assert milliseconds in {2000, 4000}

    async def rendered(*args):
        return "rendered"

    monkeypatch.setattr(ranepa_browser, "_wait_visible_state", rendered)
    page = Page()
    expected = "rendered" if failures == 2 else "connection"
    assert asyncio.run(ranepa_browser._open(page, "student", "test")) == expected
    assert page.calls == expected_calls


def test_rate_limit_is_not_retried_as_connection_failure():
    class Response:
        status = 429

    class Page:
        calls = 0
        url = "about:blank"

        async def goto(self, *args, **kwargs):
            self.calls += 1
            return Response()

    page = Page()
    assert asyncio.run(ranepa_browser._open(page, "student", "test")) == "http_429"
    assert page.calls == 1


def test_verified_exit_cache_reuses_course_but_rematches_teacher(monkeypatch):
    from studgroup.ranepa_lms import LinkCandidate

    class Invisible:
        async def is_visible(self):
            return False

    class Page:
        url = ""

        async def goto(self, *_args, **_kwargs):
            self.url = "https://lms.ranepa.ru/course/view.php?id=77"

        def get_by_text(self, *_args, **_kwargs):
            return Invisible()

        async def close(self):
            pass

    class Context:
        opened = 0

        async def new_page(self):
            self.opened += 1
            return Page()

    context = Context()
    inventory = (
        LinkCandidate(
            "https://my.mts-link.ru/j/Ranepa/111",
            "Иванов И.И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
        ),
        LinkCandidate(
            "https://my.mts-link.ru/j/Ranepa/222",
            "Петров П.П. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
        ),
    )

    async def candidates(*_args):
        return inventory, True

    monkeypatch.setattr(ranepa_browser, "_read_course_candidates", candidates)

    async def scenario():
        course_cache, exit_cache = {}, {}
        for day, teacher, expected in (
            (date(2026, 11, 23), "Иванов Иван Иванович", "111"),
            (date(2026, 11, 30), "Петров Петр Петрович", "222"),
        ):
            lesson = ExternalLesson(
                day=day,
                starts=time(9),
                ends=time(10),
                subject="История",
                teacher=teacher,
                room="СДО",
                subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45)",
            )
            result = await ranepa_browser.resolve_sdo_exits(
                context,
                ranepa_browser.BrowserResult(
                    "preview_only",
                    WeekSnapshot("A-001", day, day + timedelta(days=6), (lesson,)),
                    ("https://my.ranepa.ru/moodle_out.php?id=77",),
                ),
                course_cache=course_cache,
                exit_cache=exit_cache,
            )
            assert list(result.links.values()) == [f"https://my.mts-link.ru/j/Ranepa/{expected}"]
        assert context.opened == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("empty_reads", [0, 1, 2])
def test_moodle_dashboard_resolves_without_cabinet_exit(monkeypatch, empty_reads):
    from studgroup.ranepa_lms import LinkCandidate

    calls = []

    class Page:
        url = ""

        async def goto(self, url, **kwargs):
            self.url = url
            calls.append(url)

        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return Page()

    async def dashboard(_context, login, password):
        assert (login, password) == ("student", "password")
        calls.append("dashboard")
        return {"https://lms.ranepa.ru/course/view.php?id=77": "История России"}, None

    async def read_course(_page, _context):
        if calls.count("https://lms.ranepa.ru/course/view.php?id=77") <= empty_reads:
            return (), True
        return (
            (
                LinkCandidate(
                    "https://my.mts-link.ru/j/Ranepa/123",
                    "Иванов И.И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
                ),
            ),
            True,
        )

    monkeypatch.setattr(ranepa_browser, "_load_moodle_dashboard", dashboard)
    monkeypatch.setattr(ranepa_browser, "_read_course_candidates", read_course)

    async def scenario():
        dashboard_cache, course_cache = {}, {}
        for index in range(3):
            day = date(2026, 11, 23) + timedelta(weeks=index)
            lesson = ExternalLesson(
                day=day,
                starts=time(9),
                ends=time(10, 20),
                subject="История России",
                teacher="Иванов Иван Иванович",
                room="СДО",
                subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45)",
            )
            result = await ranepa_browser.resolve_sdo_exits(
                Context(),
                ranepa_browser.BrowserResult(
                    "preview_only",
                    WeekSnapshot("A-001", day, day + timedelta(days=6), (lesson,)),
                ),
                "student",
                "password",
                dashboard_cache=dashboard_cache,
                course_cache=course_cache,
            )
            expected = (
                []
                if index < empty_reads or empty_reads == 2
                else ["https://my.mts-link.ru/j/Ranepa/123"]
            )
            assert list(result.links.values()) == expected
        assert calls == ["dashboard"] + ["https://lms.ranepa.ru/course/view.php?id=77"] * (
            2 if empty_reads else 1
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("supplement_status", [None, 403, 429])
def test_moodle_keeps_verified_dashboard_on_supplement_timeout(supplement_status):
    from studgroup.ranepa_crawl_trace import CrawlTrace
    from studgroup.ranepa_link_states import LinkState

    class Response:
        status = supplement_status

    class Page:
        url = ""

        async def goto(self, url, **kwargs):
            self.url = url
            if url.endswith("/my/courses.php"):
                if supplement_status is None:
                    raise TimeoutError("private URL must never appear in trace")
                return Response()
            return None

        async def content(self):
            return '<a href="/course/view.php?id=77">История России</a>'

        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return Page()

    async def scenario():
        trace = CrawlTrace()
        token = ranepa_browser.browser_trace.set(trace)
        try:
            courses, error = await ranepa_browser._load_moodle_dashboard(
                Context(), "student", "password"
            )
        finally:
            ranepa_browser.browser_trace.reset(token)
        if supplement_status is None:
            assert courses == {"https://lms.ranepa.ru/course/view.php?id=77": "История России"}
            assert error is None
            assert trace.diagnostic == "timeout"
        else:
            assert courses == {}
            assert error == LinkState.NAVIGATION_ERROR
            assert trace.diagnostic == f"http_{supplement_status}"
        assert "private URL" not in str(trace.events)

    asyncio.run(scenario())
