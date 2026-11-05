"""Renderer tests — login and challenge screens are never treated as timetables."""

from studgroup.ranepa_ui import page_status, source_diagnostics


def test_challenge_is_not_calendar():
    assert page_status("https://my.ranepa.ru/lk/student/schedule", "CAPTCHA") == "captcha_required"


def test_login_redirect_and_password_input_are_not_calendar():
    assert page_status("https://lms.ranepa.ru/login/index.php", "Календарь") == "login_required"
    assert (
        page_status("https://my.ranepa.ru/lk/student/schedule", "Расписание", password_input=True)
        == "login_required"
    )


def test_rendered_page_has_only_safe_aggregate_diagnostics():
    assert page_status("https://my.ranepa.ru/lk/student/schedule", "Расписание") == "rendered"
    assert source_diagnostics(
        "Расписание. 09:00-10:20, 11:00–12:20", table_count=2, row_count=4
    ) == {
        "table_count": 2,
        "row_count": 4,
        "lesson_time_candidates": 2,
        "has_schedule_label": True,
    }
