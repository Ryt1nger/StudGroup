"""Renderer tests — login and challenge screens are never treated as timetables."""

import pytest

from studgroup.ranepa_ui import page_status, private_profile, source_diagnostics


def test_challenge_is_not_calendar():
    assert page_status("https://my.ranepa.ru/lk/student/schedule", "CAPTCHA") == "captcha_required"


@pytest.mark.parametrize(
    "body",
    [
        "Вы точно не робот",
        "ВЫ ТОЧНО НЕ РОБОТ",
        "Для подтверждения переместите ПОЛЗУНКИ",
    ],
)
def test_slider_challenge_is_not_calendar(body):
    assert page_status("https://my.ranepa.ru/lk/student/schedule", body) == "captcha_required"


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


def test_private_profile_is_directory_with_private_permissions(tmp_path):
    folder = private_profile(tmp_path / "Mac browser session")
    assert folder.exists() and folder.is_dir()
    assert folder.stat().st_mode & 0o077 == 0
    assert private_profile(folder) == folder


def test_private_profile_rejects_symlink(tmp_path):
    target = tmp_path / "actual"
    target.mkdir()
    link = tmp_path / "profile"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="profile_symlink_not_allowed"):
        private_profile(link)
