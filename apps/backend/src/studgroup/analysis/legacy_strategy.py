"""Compatibility adapter from the current extractor into the versioned C+ DTOs."""

from studgroup.analysis.contracts import (
    AnalysisInput,
    AnalysisOutput,
    Audience,
    DeadlineValue,
    EvidenceRef,
    ProposedAcademicItem,
)


class LegacyStrategy:
    def __init__(self, provider):
        self.provider = provider

    async def analyze(self, analysis_input: AnalysisInput, schedule=None):
        messages = [
            {
                "message_id": row.telegram_message_id,
                "message_date": row.message_date.isoformat(),
                "reply_to": row.reply_to_message_id,
                "topic_id": row.topic_id,
                "topic_name": row.topic_name,
                "text": row.text,
                "attachment_kinds": row.attachment_kinds,
            }
            for row in analysis_input.messages
        ]
        result = await self.provider.extract_batch(
            messages,
            analysis_input.group_timezone,
            schedule,
            target_message_id=analysis_input.target_message_ids[-1],
        )
        revisions = {
            row.telegram_message_id: row.revision for row in analysis_input.messages
        }
        proposals = []
        for item in result.batch.assignments:
            destination = "task" if item.kind == "homework" else "deadline"
            semantic_kind = (
                "other_academic_deadline" if item.kind == "needs_context" else item.kind
            )
            proposals.append(
                ProposedAcademicItem(
                    destination=destination,
                    semantic_kind=semantic_kind,
                    subject=item.subject,
                    title=item.title,
                    description=item.description,
                    deadline=DeadlineValue(
                        at=item.deadline_at,
                        date_only=item.deadline_date_only,
                        basis=self._deadline_basis(item._deadline_basis),
                    ),
                    audience=Audience(),
                    confidence=item.confidence,
                    attention_reasons=(
                        ["legacy_partial_proposal"]
                        if item.publication_state != "published"
                        else []
                    ),
                    evidence=[
                        EvidenceRef(
                            telegram_message_id=message_id,
                            revision=revisions[message_id],
                            fact_paths=["legacy_proposal"],
                        )
                        for message_id in item.source_message_ids
                    ],
                )
            )
        return AnalysisOutput(
            source_revision=analysis_input.source_revision,
            proposals=proposals,
            explicit_empty=not proposals,
        )

    @staticmethod
    def _deadline_basis(legacy_basis):
        if legacy_basis == "next_subject_lesson":
            return "schedule_inferred"
        if legacy_basis == "ambiguous_date_range":
            return "ambiguous_range"
        if legacy_basis in {
            "explicit_date",
            "relative_message_date",
            "source_day_inferred_clock",
            "model_source_context",
        }:
            return "explicit"
        return "unknown"
