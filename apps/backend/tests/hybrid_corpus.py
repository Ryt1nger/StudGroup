"""Synthetic, de-identified C+ regression corpus with gold expectations.

Each case replays a realistic DeepSeek Pass-2 answer (including its typical defects: ISO strings
instead of python objects, aliases, null deadlines, ...) through the production adapter and
strict validator. ``accepted`` states exactly which cards a student may see; everything else must
be rejected with a stable code. Only synthetic text lives here -- never real chat content.
"""

from uuid import UUID

EXISTING_ID = "11111111-1111-4111-8111-111111111111"
UNKNOWN_ID = "22222222-2222-4222-8222-222222222222"
MON = "2026-10-12T12:00:00+03:00"  # Monday


def ev(mid=10, paths=("description",), revision=1):
    return {"telegram_message_id": mid, "revision": revision, "fact_paths": list(paths)}


def item(**patch):
    base = {
        "action": "create",
        "existing_item_id": None,
        "expected_item_revision": None,
        "destination": "task",
        "semantic_kind": "homework",
        "subject": "Математика",
        "title": "Решить задачи 1–3",
        "description": "Решить задачи 1–3 из учебника.",
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "audience": {"scope": "group", "label": None},
        "confidence": 90,
        "attention_reasons": [],
        "evidence": [ev()],
    }
    return base | patch


def dl(at, basis="explicit", date_only=False):
    return {"at": at, "date_only": date_only, "basis": basis}


def accepted(
    subject,
    kind,
    at=None,
    *,
    date_only=False,
    basis="unknown",
    action="create",
    visibility="normal",
):
    return {
        "subject": subject,
        "kind": kind,
        "at": at,
        "date_only": date_only,
        "basis": basis,
        "action": action,
        "visibility": visibility,
    }


def case(
    case_id,
    messages,
    proposals,
    *,
    accepted_=(),
    rejected=(),
    existing=None,
    empty=False,
    actions=(),
    reasons=None,
):
    return {
        "id": case_id,
        "messages": messages,
        "proposals": proposals,
        "accepted": list(accepted_),
        "rejected": list(rejected),
        "existing": existing,
        "explicit_empty": empty,
        "actions": list(actions),
        "reasons": reasons or {},
    }


def msg(mid, text, when=MON):
    return (mid, when, text)


TRUSTED = [{"id": EXISTING_ID, "revision": 1}]

CASES = [
    case(
        "homework_without_deadline",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item()],
        accepted_=[accepted("Математика", "homework")],
    ),
    case(
        "homework_iso_datetime_with_offset",
        [msg(10, "Информатика: отчёт по лабораторной сдать до 16.10 23:59")],
        [
            item(
                subject="Информатика",
                title="Отчёт по лабораторной",
                description="Сдать отчёт по лабораторной.",
                deadline=dl("2026-10-16T23:59:00+03:00"),
            )
        ],
        accepted_=[accepted("Информатика", "homework", "2026-10-16T23:59", basis="explicit")],
    ),
    case(
        "homework_naive_datetime_uses_group_timezone",
        [msg(10, "Информатика: отчёт по лабораторной сдать до 16.10 23:59")],
        [
            item(
                subject="Информатика",
                title="Отчёт по лабораторной",
                description="Сдать отчёт по лабораторной.",
                deadline=dl("2026-10-16T23:59:00"),
            )
        ],
        accepted_=[accepted("Информатика", "homework", "2026-10-16T23:59", basis="explicit")],
        actions=["naive_deadline_localized"],
    ),
    case(
        "homework_utc_z_datetime",
        [msg(10, "Информатика: отчёт по лабораторной сдать до 16.10 23:59")],
        [
            item(
                subject="Информатика",
                title="Отчёт по лабораторной",
                description="Сдать отчёт по лабораторной.",
                deadline=dl("2026-10-16T20:59:00Z"),
            )
        ],
        accepted_=[accepted("Информатика", "homework", "2026-10-16T23:59", basis="explicit")],
    ),
    case(
        "homework_date_only_text",
        [msg(10, "Физика: подготовить конспект параграфа до 15.10")],
        [
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline=dl("2026-10-15"),
            )
        ],
        accepted_=[
            accepted("Физика", "homework", "2026-10-15T00:00", date_only=True, basis="explicit")
        ],
        actions=["date_text_marked_date_only"],
    ),
    case(
        "homework_date_only_flag_with_midnight",
        [msg(10, "Физика: подготовить конспект параграфа до 15.10")],
        [
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline=dl("2026-10-15T00:00:00+03:00", date_only=True),
            )
        ],
        accepted_=[
            accepted("Физика", "homework", "2026-10-15T00:00", date_only=True, basis="explicit")
        ],
    ),
    case(
        "relative_tomorrow",
        [msg(10, "Сделать презентацию по истории на завтра")],
        [
            item(
                subject="История",
                title="Презентация по истории",
                description="Сделать презентацию по истории.",
                deadline=dl("2026-10-13"),
            )
        ],
        accepted_=[
            accepted("История", "homework", "2026-10-13T00:00", date_only=True, basis="explicit")
        ],
    ),
    case(
        "relative_next_monday",
        [msg(10, "Реферат по философии нужно сдать в следующий понедельник")],
        [
            item(
                subject="Философия",
                title="Реферат по философии",
                description="Сдать реферат по философии.",
                deadline=dl("2026-10-19"),
            )
        ],
        accepted_=[
            accepted("Философия", "homework", "2026-10-19T00:00", date_only=True, basis="explicit")
        ],
    ),
    case(
        "test_deadline_kind",
        [msg(10, "Физика: тест по механике 20.10")],
        [
            item(
                destination="deadline",
                semantic_kind="test",
                subject="Физика",
                title="Тест по механике",
                description="Тест по механике.",
                deadline=dl("2026-10-20"),
            )
        ],
        accepted_=[
            accepted("Физика", "test", "2026-10-20T00:00", date_only=True, basis="explicit")
        ],
    ),
    case(
        "control_point_kind",
        [msg(10, "Контрольная точка по алгебре 20.10")],
        [
            item(
                destination="deadline",
                semantic_kind="control_point",
                subject="Алгебра",
                title="Контрольная точка по алгебре",
                description="Контрольная точка по алгебре.",
                deadline=dl("2026-10-20"),
            )
        ],
        accepted_=[
            accepted(
                "Алгебра", "control_point", "2026-10-20T00:00", date_only=True, basis="explicit"
            )
        ],
    ),
    case(
        "assessment_kind",
        [msg(10, "Самостоятельная работа по геометрии 21.10")],
        [
            item(
                destination="deadline",
                semantic_kind="assessment",
                subject="Геометрия",
                title="Самостоятельная работа по геометрии",
                description="Самостоятельная работа по геометрии.",
                deadline=dl("2026-10-21"),
            )
        ],
        accepted_=[
            accepted(
                "Геометрия", "assessment", "2026-10-21T00:00", date_only=True, basis="explicit"
            )
        ],
    ),
    case(
        "credit_kind",
        [msg(10, "Зачёт по английскому 25.12")],
        [
            item(
                destination="deadline",
                semantic_kind="credit",
                subject="Английский",
                title="Зачёт по английскому",
                description="Зачёт по английскому языку.",
                deadline=dl("2026-12-25"),
            )
        ],
        accepted_=[
            accepted("Английский", "credit", "2026-12-25T00:00", date_only=True, basis="explicit")
        ],
    ),
    case(
        "exam_kind",
        [msg(10, "Экзамен по физике 12.01")],
        [
            item(
                destination="deadline",
                semantic_kind="exam",
                subject="Физика",
                title="Экзамен по физике",
                description="Экзамен по физике.",
                deadline=dl("2027-01-12"),
            )
        ],
        accepted_=[
            accepted("Физика", "exam", "2027-01-12T00:00", date_only=True, basis="explicit")
        ],
    ),
    case(
        "other_academic_deadline_kind",
        [msg(10, "Допуск по практике нужно оформить до 30.10")],
        [
            item(
                destination="deadline",
                semantic_kind="other_academic_deadline",
                subject="Практика",
                title="Допуск по практике",
                description="Оформить допуск по практике.",
                deadline=dl("2026-10-30"),
            )
        ],
        accepted_=[
            accepted(
                "Практика",
                "other_academic_deadline",
                "2026-10-30T00:00",
                date_only=True,
                basis="explicit",
            )
        ],
    ),
    case(
        "announcement_without_academic_content",
        [msg(10, "Всем привет! Завтра пары не будет")],
        [],
        empty=True,
    ),
    case(
        "reschedule_existing_item",
        [msg(10, "Физика: тест по механике переносится на 15.10")],
        [
            item(
                action="reschedule",
                existing_item_id=EXISTING_ID,
                expected_item_revision=1,
                destination="deadline",
                semantic_kind="test",
                subject="Физика",
                title="Тест по механике",
                description="Тест по механике перенесён.",
                deadline=dl("2026-10-15"),
            )
        ],
        accepted_=[
            accepted(
                "Физика",
                "test",
                "2026-10-15T00:00",
                date_only=True,
                basis="explicit",
                action="reschedule",
            )
        ],
        existing=TRUSTED,
    ),
    case(
        "cancel_existing_item",
        [msg(10, "Контрольная точка по алгебре отменяется")],
        [
            item(
                action="cancel",
                existing_item_id=EXISTING_ID,
                expected_item_revision=1,
                destination="deadline",
                semantic_kind="control_point",
                subject="Алгебра",
                title="Контрольная точка по алгебре",
                description="Контрольная точка отменена.",
            )
        ],
        accepted_=[accepted("Алгебра", "control_point", action="cancel")],
        existing=TRUSTED,
    ),
    case(
        "update_existing_item",
        [msg(10, "Решить задачи 1–3 и ещё 4 по математике")],
        [
            item(
                action="update",
                existing_item_id=EXISTING_ID,
                expected_item_revision=1,
                title="Решить задачи 1–4",
                description="Решить задачи 1–4.",
            )
        ],
        accepted_=[accepted("Математика", "homework", action="update")],
        existing=TRUSTED,
    ),
    case(
        "reschedule_without_identity_is_held_as_unconfirmed_change",
        [msg(10, "Тест по механике переносится на 15.10")],
        [
            item(
                action="reschedule",
                destination="deadline",
                semantic_kind="test",
                deadline=dl("2026-10-15"),
            )
        ],
        accepted_=[
            accepted(
                "Математика",
                "test",
                "2026-10-15T00:00",
                date_only=True,
                basis="explicit",
                visibility="held",
            )
        ],
        actions=["change_without_identity_held"],
        reasons={0: "unconfirmed_change"},
    ),
    case(
        "cancel_without_identity_is_rejected",
        [msg(10, "Нет, тест по механике уже отменили")],
        [
            item(
                action="cancel",
                destination="deadline",
                semantic_kind="test",
                deadline=dl("2026-10-15"),
            )
        ],
        rejected=["mutation_identity_missing"],
    ),
    case(
        "mutation_identity_outside_trusted_context",
        [msg(10, "Тест по механике переносится на 15.10")],
        [
            item(
                action="reschedule",
                existing_item_id=UNKNOWN_ID,
                expected_item_revision=1,
                destination="deadline",
                semantic_kind="test",
                deadline=dl("2026-10-15"),
            )
        ],
        rejected=["existing_item_not_in_trusted_context"],
        existing=TRUSTED,
    ),
    case(
        "latest_clarification_date_wins",
        [
            msg(10, "Химия: решить задачи до 14.10"),
            msg(11, "Нет, до 16.10", "2026-10-12T13:00:00+03:00"),
        ],
        [
            item(
                subject="Химия",
                title="Решить задачи по химии",
                description="Решить задачи по химии.",
                deadline=dl("2026-10-16"),
                evidence=[ev(10), ev(11, ("deadline",))],
            )
        ],
        accepted_=[
            accepted("Химия", "homework", "2026-10-16T00:00", date_only=True, basis="explicit")
        ],
    ),
    case(
        "date_ignoring_every_literal_source_date_is_corrected_from_the_text",
        [msg(10, "Химия: решить задачи до 14.10")],
        [
            item(
                subject="Химия",
                title="Решить задачи по химии",
                description="Решить задачи по химии.",
                deadline=dl("2026-10-20"),
            )
        ],
        accepted_=[
            accepted(
                "Химия",
                "homework",
                "2026-10-14T00:00",
                date_only=True,
                basis="explicit",
                visibility="headman_attention",
            )
        ],
        reasons={0: "deadline_corrected_from_text"},
    ),
    case(
        "model_reports_conflict_without_date",
        [
            msg(10, "Химия: решить задачи до 14.10"),
            msg(11, "Или до 16.10, пока не ясно", "2026-10-12T13:00:00+03:00"),
        ],
        [
            item(
                subject="Химия",
                title="Решить задачи по химии",
                description="Решить задачи по химии.",
                attention_reasons=["conflicting_deadlines"],
                evidence=[ev(10), ev(11, ("deadline",))],
            )
        ],
        accepted_=[accepted("Химия", "homework", visibility="held")],
    ),
    case(
        "deadline_in_the_past_is_flagged",
        [msg(10, "Химия: решить задачи до 14.10")],
        [
            item(
                subject="Химия",
                title="Решить задачи по химии",
                description="Решить задачи по химии.",
                deadline=dl("2025-10-14"),
            )
        ],
        accepted_=[
            accepted(
                "Химия",
                "homework",
                "2025-10-14T00:00",
                date_only=True,
                basis="explicit",
                visibility="held",
            )
        ],
        reasons={0: "deadline_before_source"},
    ),
    case(
        "multiple_items_one_response",
        [msg(10, "Математика: задачи 1–3. Физика: конспект параграфа до 15.10")],
        [
            item(),
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline=dl("2026-10-15"),
            ),
        ],
        accepted_=[
            accepted("Математика", "homework"),
            accepted("Физика", "homework", "2026-10-15T00:00", date_only=True, basis="explicit"),
        ],
    ),
    case(
        "valid_sibling_survives_invalid_sibling",
        [msg(10, "Математика: задачи 1–3. Физика: конспект параграфа")],
        [
            item(evidence=[{"telegram_message_id": 10, "revision": 1}]),
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
            ),
        ],
        accepted_=[accepted("Физика", "homework")],
        rejected=["evidence_fact_paths_missing"],
    ),
    case(
        "exact_duplicate_proposal_is_not_doubled",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(), item()],
        accepted_=[accepted("Математика", "homework")],
        rejected=["duplicate_proposal"],
    ),
    case(
        "subgroup_with_label",
        [msg(10, "Первая подгруппа: решить задачи 1–3 по математике")],
        [item(audience={"scope": "subgroup", "label": "Подгруппа 1"})],
        accepted_=[accepted("Математика", "homework")],
    ),
    case(
        "subgroup_without_label_is_rejected",
        [msg(10, "Первая подгруппа: решить задачи 1–3 по математике")],
        [item(audience={"scope": "subgroup", "label": None})],
        rejected=["subgroup_requires_label"],
    ),
    case(
        "personal_instruction_is_held",
        [msg(10, "Иванов, подойди к преподавателю по математике")],
        [item(audience={"scope": "personal", "label": None})],
        accepted_=[accepted("Математика", "homework", visibility="held")],
    ),
    case(
        "collective_address_each_student_is_group_not_personal",
        [msg(10, "Каждый делает доклад-презентацию о выдающихся учёных России")],
        [
            item(
                subject=None,
                title="Подготовить доклад-презентацию",
                description="Каждый делает доклад-презентацию о выдающихся учёных России.",
                audience={"scope": "personal", "label": None},
            )
        ],
        accepted_=[accepted(None, "homework")],
        actions=["audience_collective_to_group"],
    ),
    case(
        "collective_address_with_named_person_stays_personal",
        [msg(10, "Всем сдать отчёт по математике. Иванов, тебе отдельно подойти к преподавателю")],
        [item(audience={"scope": "personal", "label": None})],
        accepted_=[accepted("Математика", "homework", visibility="held")],
    ),
    case(
        "next_lesson_date_computed_by_model_is_cleared_for_clarification",
        [msg(10, "К следующей паре определиться с темой проекта и командами")],
        [
            item(
                subject=None,
                title="Определиться с темой и командой проекта",
                description="Определиться с темой проекта и командами.",
                deadline=dl("2026-10-20T23:59:00+03:00"),
            )
        ],
        accepted_=[accepted(None, "homework", visibility="headman_attention")],
        actions=["unsupported_explicit_deadline_cleared"],
        reasons={0: "relative_deadline_unresolved"},
    ),
    case(
        "named_weekday_with_qualifier_keeps_the_explicit_date",
        [msg(10, "По математике сдать конспект к этой пятнице")],
        [
            item(
                title="Сдать конспект",
                description="Сдать конспект по математике.",
                deadline=dl("2026-10-16T23:59:00+03:00"),
            )
        ],
        accepted_=[accepted("Математика", "homework", "2026-10-16T23:59", basis="explicit")],
    ),
    case(
        "date_without_any_source_support_is_cleared",
        [msg(10, "Подготовить презентацию по истории")],
        [
            item(
                subject="История",
                title="Подготовить презентацию",
                description="Подготовить презентацию по истории.",
                deadline=dl("2026-10-20T23:59:00+03:00"),
            )
        ],
        accepted_=[accepted("История", "homework", visibility="headman_attention")],
        actions=["unsupported_explicit_deadline_cleared"],
        reasons={0: "deadline_not_in_source"},
    ),
    case(
        "typical_deepseek_shape_defects_are_normalized",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [
            {
                "semantic_kind": "Homework",
                "subject": "Математика",
                "title": "Решить задачи 1–3",
                "description": "Решить задачи 1–3 из учебника.",
                "deadline": None,
                "audience": None,
                "confidence": 90.0,
                "quote": "По математике решить задачи 1–3",
                "evidence": [{"source_id": 10, "fact_paths": "description"}],
            }
        ],
        accepted_=[accepted("Математика", "homework")],
        actions=[
            "enum_spelling_normalized",
            "float_to_integer",
            "destination_derived_from_kind",
            "evidence_alias_normalized",
            "single_fact_path_to_list",
            "revision_looked_up",
            "informational_key_dropped",
        ],
    ),
    case(
        "original_canary_shape_date_only_without_value",
        [msg(10, "Физика: подготовить конспект параграфа до 15.10")],
        [
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline={"at": None, "date_only": True, "basis": "explicit"},
            )
        ],
        rejected=["deadline_basis_requires_value"],
    ),
    case(
        "date_only_without_value_and_unknown_basis_is_cleared",
        [msg(10, "Физика: подготовить конспект параграфа")],
        [
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline={"at": None, "date_only": True, "basis": "unknown"},
            )
        ],
        accepted_=[accepted("Физика", "homework")],
        actions=["date_only_cleared_without_value"],
    ),
    case(
        "date_only_with_clock_time_is_contradictory",
        [msg(10, "Физика: подготовить конспект параграфа до 15.10")],
        [
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline=dl("2026-10-15T18:00:00+03:00", date_only=True),
            )
        ],
        rejected=["date_only_with_time"],
    ),
    case(
        "basis_missing_with_value_is_rejected",
        [msg(10, "Физика: подготовить конспект параграфа до 15.10")],
        [
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline={"at": "2026-10-15T18:00:00+03:00", "date_only": False},
            )
        ],
        rejected=["deadline_basis_missing"],
    ),
    case(
        "invalid_calendar_date_is_rejected",
        [msg(10, "Физика: подготовить конспект параграфа до 15.10")],
        [
            item(
                subject="Физика",
                title="Конспект параграфа",
                description="Подготовить конспект параграфа.",
                deadline=dl("2026-13-45"),
            )
        ],
        rejected=["deadline_at_invalid"],
    ),
    case(
        "ambiguous_confidence_scale_is_rejected",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(confidence=0.9)],
        rejected=["confidence_scale_ambiguous"],
    ),
    case(
        "evidence_for_nonexistent_message",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(evidence=[ev(999)])],
        rejected=["evidence_outside_envelope"],
    ),
    case(
        "evidence_with_stale_revision",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(evidence=[ev(10, revision=2)])],
        rejected=["evidence_outside_envelope"],
    ),
    case(
        "unknown_semantic_kind_is_rejected",
        [msg(10, "По математике сегодня опрос")],
        [item(semantic_kind="lecture")],
        rejected=["enum_value_not_allowed"],
    ),
    case(
        "unknown_extra_field_is_rejected",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(due_date="2026-10-15")],
        rejected=["proposal_field_not_allowed"],
    ),
    case(
        "non_object_proposal_is_rejected",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        ["homework"],
        rejected=["proposal_not_object"],
    ),
    case(
        "unsupported_fact_path_only_is_rejected",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(evidence=[ev(10, ("legacy_proposal",))])],
        rejected=["evidence_fact_paths_missing"],
    ),
    case(
        "unknown_fact_path_is_dropped_but_known_one_kept",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(evidence=[ev(10, ("description", "page_number"))])],
        accepted_=[accepted("Математика", "homework")],
        actions=["unsupported_fact_path_dropped"],
    ),
    case(
        "invented_number_falls_back_and_asks_attention",
        [msg(10, "По математике решить задачи 1–3 из учебника")],
        [item(title="Решить задачи 1–7", description="Решить задачи 1–7.")],
        accepted_=[accepted("Математика", "homework", visibility="headman_attention")],
    ),
]


def message_tuples(case_):
    return case_["messages"]


def trusted(case_):
    return None if case_["existing"] is None else case_["existing"]


assert UUID(EXISTING_ID) != UUID(UNKNOWN_ID)
