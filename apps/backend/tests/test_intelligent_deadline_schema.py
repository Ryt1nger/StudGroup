"""Schema contract for intelligent-deadline persistence (no production data required)."""

from studgroup.models import Base


def test_intelligent_deadline_tables_and_columns_are_declared_once():
    tables = Base.metadata.tables
    item_columns = tables["canonical_academic_items"].c
    assert {"deadline_reason", "timezone_confirmed", "locked_fields"} <= set(item_columns.keys())

    history = tables["canonical_deadline_history"]
    assert {
        "item_id",
        "item_revision",
        "deadline_at",
        "deadline_date_only",
        "deadline_basis",
        "deadline_reason",
        "timezone_confirmed",
        "source_telegram_message_id",
        "source_reply_to_message_id",
        "source_revision",
        "changed_at",
    } <= set(history.c.keys())
    assert {"group_no_lesson_days", "schedule_changes"} <= set(tables)


def test_schedule_changes_keep_scope_in_the_database_contract():
    constraints = Base.metadata.tables["schedule_changes"].constraints
    assert any(
        "scope IN ('group', 'personal')" in str(getattr(item, "sqltext", ""))
        for item in constraints
    )
