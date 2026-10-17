"""Provider-facing contract text generated from the backend Pydantic models.

The field list, enum values, nullability, defaults and the valid examples are *derived* from
``contracts`` so a prompt can never drift from the strict validator. Semantic rules that JSON
Schema cannot express (mirroring model validators) are written once in ``DEADLINE_RULES`` /
``PROPOSAL_RULES`` and reused by both the extraction and the repair prompts.
"""

import json
from datetime import datetime, timedelta, timezone

from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisOutput,
    Audience,
    DeadlineValue,
    EvidenceRef,
    ProposedAcademicItem,
)


def _resolve(node, defs):
    while "$ref" in node:
        node = defs[node["$ref"].rsplit("/", 1)[-1]]
    return node


def _render(node, defs):
    node = _resolve(node, defs)
    if "enum" in node:
        return "|".join(json.dumps(v) for v in node["enum"])
    if "const" in node:
        return json.dumps(node["const"])
    if "anyOf" in node:
        parts = [_render(option, defs) for option in node["anyOf"]]
        return "|".join(dict.fromkeys(parts))
    kind = node.get("type")
    if kind == "object":
        required = set(node.get("required", ()))
        fields = ", ".join(
            f"{name}{'' if name in required else '?'}: {_render(child, defs)}"
            for name, child in node.get("properties", {}).items()
        )
        return "{" + fields + "}"
    if kind == "array":
        return "[" + _render(node.get("items", {}), defs) + "]"
    if kind == "string":
        if node.get("format") == "date-time":
            return "ISO-8601 datetime"
        if node.get("format") == "uuid":
            return "uuid"
        return "string"
    return {"integer": "integer", "boolean": "boolean", "null": "null", "number": "number"}.get(
        kind, "any"
    )


def render_contract(model=AnalysisOutput) -> str:
    """Compact type signature of a model (``?`` marks optional keys)."""
    schema = model.model_json_schema()
    return _render(schema, schema.get("$defs", {}))


DEADLINE_RULES = (
    (
        "deadline.at is an ISO-8601 datetime in group_timezone (an offset-less value is read in "
        "that timezone); deadline.date_only=true means only the calendar date is known: write at as "
        'that date, e.g. "2026-10-15" or midnight local time.'
    ),
    (
        "deadline.basis is explicit only when the message text writes a calendar date or a "
        "calendar word you can resolve from that message's local_date and local_weekday "
        '("завтра", "послезавтра", "в пятницу"). A reference to the next lesson '
        '("к следующей паре", "к следующему занятию", "на следующей неделе") is NOT a '
        "date: use unknown with at=null, never compute a date from a guessed timetable. "
        "schedule_inferred only when trusted context supplies the date, ambiguous_range for a "
        "range of dates, otherwise unknown."
    ),
    (
        "basis explicit or schedule_inferred REQUIRES a non-null at; date_only=true REQUIRES a "
        'non-null at. When no date is stated use exactly {"at": null, "date_only": false, '
        '"basis": "unknown"}. Never guess a date to fill a gap.'
    ),
)

PROPOSAL_RULES = (
    ("destination is task for semantic_kind homework and deadline for every other semantic_kind."),
    (
        "action=create requires existing_item_id and expected_item_revision to be null. update, "
        "reschedule and cancel require both, copied exactly from trusted existing_items; if the "
        "item is not listed there, do not emit that proposal."
    ),
    (
        "Every proposal needs at least one evidence entry: telegram_message_id and revision copied "
        "from the supplied messages and fact_paths naming only the non-null fields of this proposal "
        "that the message supports (subject, title, description, deadline, audience)."
    ),
    (
        "Optional text stays null when unknown; never write placeholder words. confidence is an "
        "integer 0-100."
    ),
)


def _example_output() -> dict:
    tz = timezone(timedelta(hours=3))
    homework = ProposedAcademicItem(
        destination="task",
        semantic_kind="homework",
        subject="Математика",
        title="Решить задачи 1–3",
        description="Решить задачи 1–3 из задания.",
        deadline=DeadlineValue(),
        audience=Audience(),
        confidence=90,
        evidence=[
            EvidenceRef(telegram_message_id=101, revision=1, fact_paths=["subject", "description"])
        ],
    )
    dated = ProposedAcademicItem(
        destination="deadline",
        semantic_kind="test",
        subject="Физика",
        title="Тест по механике",
        description="Тест по теме «Механика».",
        deadline=DeadlineValue(
            at=datetime(2026, 10, 20, 0, 0, tzinfo=tz), date_only=True, basis="explicit"
        ),
        confidence=85,
        evidence=[
            EvidenceRef(telegram_message_id=102, revision=1, fact_paths=["title", "deadline"])
        ],
    )
    timed = ProposedAcademicItem(
        destination="task",
        semantic_kind="homework",
        subject="Информатика",
        title="Отчёт по лабораторной",
        description="Сдать отчёт по лабораторной работе.",
        deadline=DeadlineValue(
            at=datetime(2026, 10, 16, 23, 59, tzinfo=tz), date_only=False, basis="explicit"
        ),
        confidence=92,
        evidence=[
            EvidenceRef(telegram_message_id=103, revision=1, fact_paths=["description"]),
            EvidenceRef(telegram_message_id=104, revision=1, fact_paths=["deadline"]),
        ],
    )
    output = AnalysisOutput(
        source_revision=1, proposals=[homework, dated, timed], explicit_empty=False
    )
    return output.model_dump(mode="json")


def example_output() -> dict:
    """Three valid sample proposals; tests re-validate them through the production path."""
    return _example_output()


def contract_block() -> str:
    example = json.dumps(_example_output(), ensure_ascii=False, separators=(",", ":"))
    rules = "\n".join(f"- {rule}" for rule in (*PROPOSAL_RULES, *DEADLINE_RULES))
    return (
        "Return JSON only, exactly this contract (generated from the backend models; `?` marks "
        "an optional key, keys not listed are forbidden):\n"
        f"{render_contract()}\n"
        f'Copy schema_version "{ANALYSIS_SCHEMA_VERSION}", engine_version '
        f'"{ANALYSIS_ENGINE_VERSION}" and source_revision exactly from the request. With no '
        "academic items return proposals [] and explicit_empty true; otherwise explicit_empty "
        "false.\n"
        f"Rules:\n{rules}\n"
        "Valid example (ids, subjects and dates are illustrative only; never copy them):\n"
        f"{example}"
    )


def proposal_contract_block() -> str:
    """Contract for one proposal (used by AI Repair); same generated rules and example."""
    example = json.dumps(
        _example_output()["proposals"][1], ensure_ascii=False, separators=(",", ":")
    )
    rules = "\n".join(f"- {rule}" for rule in (*PROPOSAL_RULES, *DEADLINE_RULES))
    return (
        "Target contract of ONE proposal (generated from the backend models; `?` marks an "
        "optional key, keys not listed are forbidden):\n"
        f"{render_contract(ProposedAcademicItem)}\n"
        f"Rules:\n{rules}\n"
        f"Valid example (illustrative values only): {example}"
    )
