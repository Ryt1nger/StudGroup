"""The local diagnostic must not print raw HTML or identifiable lesson fields."""

from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ranepa_html_check.py"
spec = spec_from_file_location("ranepa_html_check", SCRIPT)
check = module_from_spec(spec)
spec.loader.exec_module(check)


def test_private_file_is_read_without_exposing_lesson_fields(tmp_path):
    html = (
        "<html><body><h1>Э-ОБ-385-04-26-02</h1><h2>11/2/2026 Понедельник</h2>"
        "<div>09:00-10:20</div><div>305</div><div>Секретный предмет</div>"
        "<div>Лекционные занятия</div><div>Частное имя</div>"
        "<div>Э-ОБ-385-04-26-02</div></body></html>"
    )
    path = tmp_path / "private.html"
    path.write_text(html)
    report = check.inspect_html(check.local_html(path), "Э-ОБ-385-04-26-02", date(2026, 11, 2))
    assert report == {
        "status": "preview_only",
        "complete": False,
        "lesson_count": 1,
        "days_with_lessons": 1,
    }
    assert "Секретный" not in str(report) and "Частное" not in str(report)


def test_repository_file_and_symlink_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="html_inside_repository_not_allowed"):
        check.local_html(SCRIPT)
    path = tmp_path / "source.html"
    path.write_text("<html/>")
    link = tmp_path / "link.html"
    link.symlink_to(path)
    with pytest.raises(ValueError, match="html_symlink_not_allowed"):
        check.local_html(link)
