"""Links observed in the live LMS layout are matched by exact stream and teacher."""

from datetime import date, time

import pytest

from studgroup.ranepa_lms import course_detail_pages, course_links, meeting_url, select_lesson_link
from studgroup.ranepa_probe import SourceAccessError


def fixture(*labels):
    activities = "".join(
        f'<a href="/mod/url/view.php?id={index}">{label}</a>'
        for index, label in enumerate(labels, 100)
    )
    return (
        '<main class="course-content"><a href="/course/view.php?id=85813">Курс</a>'
        + activities
        + '<a href="/mod/quiz/view.php?id=9">Тест</a><a href="https://evil.example/mod/url/view.php?id=1">Посторонняя ссылка</a></main>'
    )


def test_exact_group_and_teacher_select_one_permanent_course_link():
    links = course_links(
        fixture(
            "Клишина О. С._У-ОБ26-СГЯ-лек(13,14,90,91)_постоянная ссылка",
            "Клишина О. С._Э-ОБ26-СГЯ-лек(43,44,45)_постоянная ссылка",
            "Дёмина Т.М. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
        )
    )
    choice = select_lesson_link(
        links,
        teacher="Клишина Ольга Семеновна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ",
        day=date(2026, 11, 2),
    )
    assert choice.state == "matched"
    assert choice.url == "https://lms.ranepa.ru/mod/url/view.php?id=101"


def test_dated_link_precedes_permanent_and_ambiguity_is_not_published():
    links = course_links(
        fixture(
            "Алпеев О.Е. 09.11.2026 Э-ОБ26-СГЯ-лек(43,44,45)",
            "Алпеев О.Е. 16.11.2026 Э-ОБ26-СГЯ-лек(43,44,45)",
            "Алпеев О.Е. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
        )
    )
    choice = select_lesson_link(
        links,
        teacher="Алпеев Олег Евгеньевич",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 9),
    )
    assert choice.state == "matched" and choice.url.endswith("id=100")
    other = select_lesson_link(
        links[:2],
        teacher="Алпеев Олег Евгеньевич",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 9),
    )
    assert other.state == "matched" and other.url.endswith("id=100")
    ambiguous = select_lesson_link(
        links[1:],
        teacher="Алпеев Олег Евгеньевич",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 9),
    )
    assert ambiguous.state == "matched" and ambiguous.url.endswith("id=102")
    doubled = select_lesson_link(
        course_links(
            fixture(
                "Федотова Е.А. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
                "Федотова Е.А. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка 2",
            )
        ),
        teacher="Федотова Елена Алексеевна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 9),
    )
    assert doubled.state == "ambiguous" and doubled.url is None


def test_no_group_evidence_or_malformed_page_fails_closed():
    links = course_links(fixture("Клишина О. С. постоянная ссылка"))
    assert (
        select_lesson_link(
            links,
            teacher="Клишина Ольга Семеновна",
            group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
            day=date(2026, 11, 2),
        ).state
        == "not_found"
    )
    with pytest.raises(SourceAccessError, match="lms_course_unverified"):
        course_links("<main>Авторизация</main>")


def test_identity_matches_without_date_or_permanent_evidence_remain_unverified():
    links = course_links(
        fixture(
            "Федотова Е.А. Э-ОБ26-СГЯ-лек(43,44,45)",
            "Федотова Е.А. Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ",
        )
    )
    choice = select_lesson_link(
        links,
        teacher="Федотова Елена Алексеевна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ",
        day=date(2026, 11, 9),
    )
    assert choice.state == "timing_unverified" and choice.url is None
    assert len(choice.candidates) == 2


def test_two_digit_date_and_start_time_disambiguate_same_day():
    links = course_links(
        fixture(
            "Иванова И.И. 12.11.26 09.00-10.20 Э-ОБ26-СГЯ-лек(43,44,45)",
            "Иванова И.И. 12.11.26 10.40-12.00 Э-ОБ26-СГЯ-лек(43,44,45)",
        )
    )
    choice = select_lesson_link(
        links,
        teacher="Иванова Ирина Ивановна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 12),
        starts=time(10, 40),
    )
    assert choice.state == "matched" and choice.url.endswith("id=101")


def test_course_url_is_canonical_without_session_parameters():
    html = fixture("Иванова И.И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка")
    html = html.replace("id=100", "id=100&sesskey=private")
    assert course_links(html)[0].url == "https://lms.ranepa.ru/mod/url/view.php?id=100"


def test_mts_meeting_activity_can_be_selected_with_exact_identity():
    html = (
        '<main class="course-content"><a href="/course/view.php?id=1">Курс</a>'
        '<a href="/mod/mtslinkrnhgs/view.php?id=1329503&sesskey=private">'
        "Федотова Е.А. Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ постоянная ссылка</a>"
        '<a href="/mod/mtslinkrnhgs/view.php?id=9">'
        "Федотова Е.А. Г-ОБ26-СГЯ-лек(46,47,48)</a></main>"
    )
    choice = select_lesson_link(
        course_links(html),
        teacher="Федотова Елена Алексеевна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ",
        day=date(2026, 11, 9),
    )
    assert choice.url == "https://lms.ranepa.ru/mod/mtslinkrnhgs/view.php?id=1329503"


def test_teacher_surname_must_match_whole_word():
    links = course_links(fixture("Иванова И.И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка"))
    choice = select_lesson_link(
        links,
        teacher="Иванов Иван Иванович",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 12),
    )
    assert choice.state == "not_found"


def test_collapsed_course_details_provide_teacher_context_for_generic_join_label():
    html = (
        '<main class="course-content"><a href="/course/view.php?id=85813">Курс</a>'
        "<details><summary>Клишина О. С. Э-ОБ26-СГЯ-лек(43,44,45) "
        "постоянная ссылка</summary><p>"
        '<a href="/mod/url/view.php?id=200&sesskey=secret">Подключиться</a>'
        "</p></details>"
        "<details><summary>Дёмина Т. М. Э-ОБ26-СГЯ-лек(43,44,45) "
        "постоянная ссылка</summary><p>"
        '<a href="/mod/url/view.php?id=201">Подключиться</a>'
        "</p></details></main>"
    )
    all_links = course_links(html)
    assert len(all_links) == 2
    assert all("secret" not in candidate.url for candidate in all_links)
    choice = select_lesson_link(
        all_links,
        teacher="Клишина Ольга Семеновна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 19),
    )
    assert choice.state == "matched"
    assert choice.url == "https://lms.ranepa.ru/mod/url/view.php?id=200"


def test_multiple_teachers_and_direct_meeting_urls_on_nested_moodle_text_page():
    course = (
        '<main class="course-content"><a href="/course/view.php?id=85813">Курс</a>'
        '<a href="/mod/page/view.php?id=77&sesskey=secret">Внутренняя страница</a>'
        '<a href="/mod/book/view.php?id=88">Книга</a>'
        '<a href="/course/section.php?id=99">Раздел</a>'
        '<a href="https://evil.example/mod/page/view.php?id=3">Постороннее</a>'
        "</main>"
    )
    assert course_detail_pages(course) == (
        "https://lms.ranepa.ru/mod/page/view.php?id=77",
        "https://lms.ranepa.ru/mod/book/view.php?id=88",
        "https://lms.ranepa.ru/course/section.php?id=99",
    )
    nested = (
        "<main><p>Федотова Е. А. Э-ОБ26-СГЯ-лек(43,44,45) "
        "постоянная ссылка https://my.mts-link.ru/j/Ranepa/98765</p>"
        "<p>Клишина О. С. Э-ОБ26-СГЯ-лек(43,44,45) "
        '<a href="https://us06web.zoom.us/j/123456">постоянная ссылка</a></p>'
        "</main>"
    )
    links = course_links(nested, require_course=False)
    assert len(links) == 2
    choice = select_lesson_link(
        links,
        teacher="Федотова Елена Алексеевна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 19),
    )
    assert choice.url == "https://my.mts-link.ru/j/Ranepa/98765"
    assert choice.state == "matched"


def test_links_from_untrusted_hosts_and_file_downloads_are_not_candidates():
    html = (
        '<main class="course-content"><a href="/course/view.php?id=11">Курс</a>'
        '<a href="https://zoom.us.evil.example/j/123">Иванов поток постоянная</a>'
        '<a href="https://lms.ranepa.ru/mod/quiz/view.php?id=12">Тест</a>'
        '<a href="https://evil.example/mod/url/view.php?id=19">Не наш курс</a>'
        '<a href="http://my.mts-link.ru/j/123">Не HTTPS</a></main>'
    )
    assert course_links(html) == ()
    assert meeting_url("https://lms.ranepa.ru/mod/url/view.php?id=15&sesskey=secret") == (
        "https://lms.ranepa.ru/mod/url/view.php?id=15"
    )
    assert meeting_url("https://evil.example/mod/url/view.php?id=15") is None


def test_abbreviated_semester_date_list_is_matched_for_actual_lesson_day():
    label = (
        "Алпеев О.Е. Ссылка на занятия на понедельник "
        "(14, 21, 28.09; 05, 12, 19.10; 9, 16, 23.11) "
        "и четверг (24.09; 22.10; 19.11) "
        "для группы Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ"
    )
    links = course_links(fixture(label))
    for when in (date(2026, 11, 9), date(2026, 11, 16), date(2026, 11, 19)):
        choice = select_lesson_link(
            links,
            teacher="Алпеев Олег Евгеньевич",
            group_label="Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ",
            day=when,
        )
        assert choice.state == "matched"
        assert choice.url == "https://lms.ranepa.ru/mod/url/view.php?id=100"
    other_day = select_lesson_link(
        links,
        teacher="Алпеев Олег Евгеньевич",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 20),
    )
    assert other_day.url is None


def test_single_exact_teacher_and_stream_meeting_without_permanent_word_is_matched():
    links = course_links(
        fixture(
            "Федотова Е. А._Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ Гиперссылка",
            "Федотова Е. А._Г-ОБ26-СГЯ-лек(46,47,48,49,63,70,71,72) Гиперссылка",
            "Дёмина Т. М._Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ Гиперссылка",
        )
    )
    choice = select_lesson_link(
        links,
        teacher="Федотова Елена Алексеевна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ",
        day=date(2026, 11, 23),
    )
    assert choice.state == "matched"
    assert choice.url.endswith("id=100")
    choice_other = select_lesson_link(
        links,
        teacher="Федотова Елена Алексеевна",
        group_label="Г-ОБ26-СГЯ-лек(46,47,48,49,63,70,71,72)",
        day=date(2026, 11, 23),
    )
    assert choice_other.url.endswith("id=101")


def test_undated_same_teacher_stream_with_two_distinct_meetings_is_not_guessed():
    links = course_links(
        fixture(
            "Федотова Е.А. Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ Гиперссылка",
            "Федотова Е.А. Э-ОБ26-СГЯ-лек(43,44,45) / ОБИ Гиперссылка",
        )
    )
    choice = select_lesson_link(
        links,
        teacher="Федотова Елена Алексеевна",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 23),
    )
    assert choice.url is None
    assert choice.state == "timing_unverified"


def test_course_detail_pages_prioritize_later_meeting_page_over_irrelevant_handouts():
    links = "".join(f'<a href="/mod/page/view.php?id={i}">Материал {i}</a>' for i in range(30))
    html = (
        '<main class="course-content"><a href="/course/view.php?id=1">Курс</a>'
        + links
        + '<a href="/mod/page/view.php?id=444">Ссылки на онлайн-занятия</a></main>'
    )
    urls = course_detail_pages(html)
    assert len(urls) == 16
    assert urls[0] == "https://lms.ranepa.ru/mod/page/view.php?id=444"
    assert "https://lms.ranepa.ru/mod/page/view.php?id=0" in urls


def test_explicitly_one_off_undated_link_does_not_become_permanent():
    links = course_links(
        fixture("Иванов И.И. Э-ОБ26-СГЯ-лек(43,44,45) разовая конференция")
    )
    choice = select_lesson_link(
        links,
        teacher="Иванов Иван Иванович",
        group_label="Э-ОБ26-СГЯ-лек(43,44,45)",
        day=date(2026, 11, 23),
    )
    assert choice.url is None
    assert choice.state == "timing_unverified"
