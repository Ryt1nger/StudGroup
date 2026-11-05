"""Network-free tests with a synthetic Moodle response (not personal account data)."""

import asyncio

import httpx
import pytest

from studgroup.ranepa_lms import (
    RanepaLmsError,
    fetch_lecture_activities,
    parse_course_id,
    parse_lecture_activities,
)

COURSE = "https://lms.ranepa.ru/course/view.php?id=89093"


def test_course_url_is_strict_and_host_pinned():
    assert parse_course_id(COURSE) == 89093
    for bad in [
        "http://lms.ranepa.ru/course/view.php?id=89093",
        "https://lms.ranepa.ru.evil.org/course/view.php?id=89093",
        "https://lms.ranepa.ru/course/view.php?id=89093&sesskey=secret",
        "https://lms.ranepa.ru/course/view.php?id=89093&id=8",
        "https://user:pass@lms.ranepa.ru/course/view.php?id=89093",
        "https://lms.ranepa.ru/course/view.php?id=0",
        "https://lms.ranepa.ru/course/view.php?id=89093#fragment",
    ]:
        with pytest.raises(ValueError):
            parse_course_id(bad)


def test_extract_activity_links_distinguishes_duplicate_titles_from_duplicate_urls():
    sections = [{
        "name": "Ссылки на лекции в СДО",
        "modules": [
            {"id": 46, "modname": "url", "name": "Федотова Е. А._Г-лек(46,47)",
             "url": "https://lms.ranepa.ru/mod/url/view.php?id=46"},
            {"id": 47, "modname": "url", "name": "Федотова Е. А._Г-лек(46,47)",
             "url": "https://lms.ranepa.ru/mod/url/view.php?id=47"},
            {"id": 46, "modname": "url", "name": "duplicate content",
             "url": "https://lms.ranepa.ru/mod/url/view.php?id=46"},
            {"id": 50, "modname": "resource", "name": "Конспект лекции",
             "url": "https://lms.ranepa.ru/mod/resource/view.php?id=50"},
            {"id": 51, "modname": "quiz", "name": "Контрольная точка",
             "url": "https://lms.ranepa.ru/mod/quiz/view.php?id=51"},
        ],
    }]
    result = parse_lecture_activities(sections, course_id=89093)
    assert [l.module_id for l in result] == [46, 47]
    assert all(l.course_id == 89093 for l in result)


def test_skip_hidden_resources_external_links_and_invalid_moodle_activity():
    result = parse_lecture_activities([{"name": "Ссылки на лекции", "modules": [
        {"id": 1, "modname": "url", "name": "Hidden", "visible": 0,
         "url": "https://lms.ranepa.ru/mod/url/view.php?id=1"},
        {"id": 2, "modname": "url", "name": "Inaccessible", "uservisible": False,
         "url": "https://lms.ranepa.ru/mod/url/view.php?id=2"},
        {"id": 3, "modname": "url", "name": "Phishing",
         "url": "https://lms.ranepa.ru.evil.com/mod/url/view.php?id=3"},
        {"id": 4, "modname": "url", "name": "Token leak",
         "url": "https://lms.ranepa.ru/mod/url/view.php?id=4&sesskey=abc"},
        {"id": 5, "modname": "url", "name": "Mismatched id",
         "url": "https://lms.ranepa.ru/mod/url/view.php?id=7"},
        {"id": 6, "modname": "url", "name": "Good",
         "url": "https://lms.ranepa.ru/mod/url/view.php?id=6"},
    ]}], course_id=89093)
    assert [x.module_id for x in result] == [6]


def test_no_links_outside_lecture_sections_unless_title_explicit():
    result = parse_lecture_activities([{"name": "Материалы", "modules": [
        {"id": 1, "modname": "url", "name": "Полезный сайт",
         "url": "https://lms.ranepa.ru/mod/url/view.php?id=1"},
        {"id": 2, "modname": "url", "name": "Вебинар по теме",
         "url": "https://lms.ranepa.ru/mod/url/view.php?id=2"},
    ]}], course_id=89093)
    assert [x.module_id for x in result] == [2]


def test_readonly_moodle_api_request_has_no_redirect_and_never_returns_token():
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(200, json=[{"name": "Лекции", "modules": [
            {"id": 9, "modname": "url", "name": "Лекция 1",
             "url": "https://lms.ranepa.ru/mod/url/view.php?id=9"}
        ]}])

    result = asyncio.run(fetch_lecture_activities(
        COURSE, token="fixture-only", transport=httpx.MockTransport(responder)))
    assert len(result) == 1 and result[0].module_id == 9
    assert calls[0].method == "POST"
    assert str(calls[0].url) == "https://lms.ranepa.ru/webservice/rest/server.php"
    assert b"core_course_get_contents" in calls[0].content
    assert b"fixture-only" in calls[0].content
    assert "fixture-only" not in repr(result)


def test_api_denial_or_redirect_fails_closed_without_making_links():
    for response in (
        httpx.Response(200, json={"exception": "invalid_token", "message": "private"}),
        httpx.Response(302, headers={"location": "https://unknown.example/"}),
        httpx.Response(200, text="<html>Login</html>"),
    ):
        def responder(_request):
            return response

        with pytest.raises(RanepaLmsError) as e:
            asyncio.run(fetch_lecture_activities(
                COURSE, token="sensitive", transport=httpx.MockTransport(responder)))
        assert "sensitive" not in str(e.value)
        assert "private" not in str(e.value)
