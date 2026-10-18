"""Apply the deadline policy to one validated proposal (pure; the database is the caller's job)."""

from dataclasses import dataclass, field

from studgroup.analysis.deadline_policy import DeadlineDecision, decide_deadline, headman_request
from studgroup.deadlines import ScheduleDeadlineContext

REASON_SET_DEADLINE = "deadline_needs_headman"
REASON_CONFIRM_DEADLINE = "deadline_inferred_confirm"


@dataclass(frozen=True)
class DeadlineContext:
    """Everything the policy needs besides the proposal; built once per group and run."""

    timezone: str
    timezone_confirmed: bool = True
    schedule: ScheduleDeadlineContext | None = None
    no_lesson_days: frozenset = frozenset()


@dataclass(frozen=True)
class AppliedDeadline:
    decision: DeadlineDecision
    timezone_confirmed: bool
    request: str | None
    reasons: list = field(default_factory=list)  # attention reasons to add for the headman


def apply_policy(proposal, messages, subject, context):
    """Decide the deadline of ``proposal`` from its own cited messages (never the processing clock)."""
    cited = {row.telegram_message_id for row in proposal.evidence}
    sources = [
        (m.text, m.message_date) for m in messages if m.telegram_message_id in cited and m.text
    ]
    model = (proposal.deadline.at, proposal.deadline.date_only) if proposal.deadline.at else None
    if not sources:  # attachment-only evidence proves no date (spec section 5)
        decision = DeadlineDecision(None, False, "unknown", "no_text_source")
    else:
        decision = decide_deadline(
            kind=proposal.semantic_kind,
            sources=sources,
            timezone=context.timezone,
            subject=subject,
            schedule=context.schedule,
            model_deadline=model,
            no_lesson_days=context.no_lesson_days,
        )
    request = headman_request(decision, proposal.semantic_kind)
    reasons = []
    if request == "set_deadline":
        reasons.append(REASON_SET_DEADLINE)
    elif request == "confirm_inferred_deadline":
        reasons.append(REASON_CONFIRM_DEADLINE)
    if not context.timezone_confirmed and decision.at is not None:
        reasons.append("timezone_unconfirmed")
    return AppliedDeadline(decision, context.timezone_confirmed, request, reasons)
