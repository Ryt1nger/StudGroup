"""Opt-in PostgreSQL run of the cabinet API: row locks, UPDATE ... RETURNING and upserts."""

import test_staff_api as staff
from test_postgres_student_card_api import pytestmark  # noqa: F401  (skip without PostgreSQL)

pytest_plugins = ["test_postgres_student_card_api"]


def test_postgres_lesson_change_is_audited_and_versioned(postgres_client):
    staff.test_recurring_lesson_change_is_audited_and_stale_revisions_are_rejected(postgres_client)


def test_postgres_days_without_lessons(postgres_client):
    staff.test_days_without_lessons(postgres_client)


def test_postgres_group_settings(postgres_client):
    staff.test_group_settings_are_headman_only_and_versioned(postgres_client)


def test_postgres_confirmed_deadline_reaches_the_student(postgres_client):
    staff.test_students_see_the_neutral_label_and_then_the_confirmed_deadline(postgres_client)


def test_postgres_set_deadline_survives_a_schedule_rebuild(postgres_client):
    staff.test_setting_a_date_only_deadline_locks_it_against_a_schedule_rebuild(postgres_client)
