"""The month browser renews a session that expires during navigation."""

import asyncio
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from studgroup import ranepa_browser
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot


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
