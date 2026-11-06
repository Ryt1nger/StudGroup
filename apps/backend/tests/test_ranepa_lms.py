"""Links observed in the live LMS layout are matched by exact stream and teacher."""

from datetime import date

import pytest

from studgroup.ranepa_lms import course_links, select_lesson_link
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
