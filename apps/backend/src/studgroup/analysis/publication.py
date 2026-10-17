"""Application port and a no-side-effect publisher for replay and shadow mode."""

from typing import Protocol

from studgroup.analysis.contracts import ApplicationOutcome, ValidationResult


class AnalysisPublisher(Protocol):
    async def apply(self, result: ValidationResult) -> list[ApplicationOutcome]: ...


class ShadowPublisher:
    """Produce auditable decisions without accepting database or notifier dependencies."""

    async def apply(self, result: ValidationResult) -> list[ApplicationOutcome]:
        outcomes = [
            ApplicationOutcome(
                source_revision=result.source_revision,
                proposal_ordinal=ordinal,
                technical_status="parsed",
                application_status="shadow_only",
                reason_code="shadow_proposal_validated",
            )
            for ordinal, _proposal in zip(result.accepted_ordinals, result.accepted, strict=True)
        ]
        outcomes.extend(
            ApplicationOutcome(
                source_revision=result.source_revision,
                proposal_ordinal=row.proposal_ordinal,
                technical_status="rejected",
                application_status="not_applied",
                reason_code=row.reason_code,
            )
            for row in result.rejected
        )
        if result.explicit_empty:
            outcomes.append(
                ApplicationOutcome(
                    source_revision=result.source_revision,
                    proposal_ordinal=-1,
                    technical_status="parsed",
                    application_status="shadow_only",
                    reason_code="valid_empty",
                )
            )
        return outcomes
