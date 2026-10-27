"""One group-scoped image input shared by live packages and shadow snapshots."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from studgroup.analysis.image_contracts import ImageEvidence, VisualReading
from studgroup.models import ImageAnalysisJob, MessageAttachment


async def load_image_evidence(db, group_id, messages, now=None, since=None):
    now = now or datetime.now(UTC)
    revisions = {row.telegram_message_id: row.revision for row in messages}
    from studgroup.models import RawMessage

    attachments = (await db.execute(select(MessageAttachment, RawMessage).join(
        RawMessage, RawMessage.id == MessageAttachment.raw_message_id
    ).where(
        MessageAttachment.group_id == group_id, RawMessage.group_id == group_id,
        RawMessage.telegram_message_id.in_(revisions), MessageAttachment.active.is_(True),
        MessageAttachment.delete_at > now, RawMessage.delete_at > now,
    ).order_by(RawMessage.telegram_message_id, MessageAttachment.created_at, MessageAttachment.id))).all()
    result = []
    from studgroup.analysis.image_jobs import supported

    for attachment, raw in attachments:
        if not supported(attachment) or raw.revision != revisions[raw.telegram_message_id]:
            continue
        jobs = (await db.scalars(select(ImageAnalysisJob).where(
            ImageAnalysisJob.group_id == group_id, ImageAnalysisJob.attachment_id == attachment.id,
            ImageAnalysisJob.source_revision == raw.revision, ImageAnalysisJob.delete_at > now,
            ImageAnalysisJob.state != "stale",
        ).order_by(ImageAnalysisJob.pass_number.desc(), ImageAnalysisJob.created_at.desc(), ImageAnalysisJob.id))).all()
        job = jobs[0] if jobs else None
        # A alone is never complete contextual evidence.
        state = job.state if job and (job.pass_number == 2 or job.state == "failed") else "pending"
        error = job.error if job else "image_not_scheduled"
        if since is not None:
            from studgroup.analysis.image_jobs import utc
            if utc(raw.message_date) < utc(since) and job is None:
                state, error = "failed", "outside_activation_scope"
        if job and state == "ready":
            from studgroup.analysis.image_schedule import current_source
            from studgroup.api import ApiError
            try:
                await current_source(db, job.id, group_id, now)
            except ApiError:
                state, error = "pending", "context_or_source_changed"
        reading = VisualReading.model_validate_json(job.result_json) if job and state == "ready" else None
        result.append(ImageEvidence(
            attachment_id=attachment.id, telegram_message_id=raw.telegram_message_id,
            source_revision=raw.revision, context_sha256=job.context_sha256 if job else "0" * 64,
            state=state, reading=reading, error=error,
        ))
    return result


def add_image_facts(messages, evidence):
    result = []
    for message in messages:
        facts = [fact.text for item in evidence
                 if item.telegram_message_id == message.telegram_message_id and item.reading
                 for fact in item.reading.facts if fact.certainty == "confirmed"]
        result.append(message.model_copy(update={"image_facts": list(dict.fromkeys(facts))}))
    return result


def proposal_image_problem(proposal, data):
    images = {str(item.attachment_id): item for item in data.image_evidence}
    cited = {ref.telegram_message_id for ref in proposal.evidence}
    dependent = []
    for ref in proposal.evidence:
        for path in ref.fact_paths:
            if not path.startswith("image:"):
                continue
            parts = path.split(":")
            try:
                ident = str(uuid.UUID(parts[1]))
                index = int(parts[2])
            except (ValueError, IndexError):
                return "invalid_image_fact_reference"
            item = images.get(ident)
            if (item is None or item.telegram_message_id != ref.telegram_message_id
                    or item.source_revision != ref.revision or item.state != "ready" or item.reading is None
                    or index < 0 or index >= len(item.reading.facts)
                    or item.reading.facts[index].certainty != "confirmed"):
                return "unverified_image_fact"
            dependent.append(item)
    # Bare pictures cannot become homework even if a model forgets image fact references.
    empty_sources = {row.telegram_message_id for row in data.messages if not row.text.strip()}
    dependent.extend(item for item in data.image_evidence
                     if item.telegram_message_id in cited & empty_sources)
    if proposal.semantic_kind == "homework":
        classwork = [item for item in data.image_evidence
                     if item.telegram_message_id in cited and item.reading
                     and "in_class_task" in item.reading.semantic_types
                     and "homework" not in item.reading.semantic_types]
        dependent.extend(classwork)
    if dependent:
        needed = "homework" if proposal.semantic_kind == "homework" else "assessment"
        if any(item.state != "ready" or item.reading is None for item in dependent):
            return "image_evidence_unavailable"
        if not any(needed in item.reading.semantic_types for item in dependent):
            return "image_purpose_unconfirmed"
    return None


def fact_source(message):
    """Ephemeral validation view; original message text/evidence is never changed."""
    facts = getattr(message, "image_facts", [])
    return message.model_copy(update={"text": "\n".join([message.text, *facts])}) if facts else message


async def display_metadata(db, group_id, evidence):
    """Common metadata for owner preview and student API; only successful pass B is eligible."""
    from studgroup.analysis.image_jobs import digest

    result = {}
    for item in evidence:
        jobs = (await db.scalars(select(ImageAnalysisJob).where(
            ImageAnalysisJob.group_id == group_id, ImageAnalysisJob.attachment_id == item.attachment_id,
            ImageAnalysisJob.source_revision == item.source_revision,
            ImageAnalysisJob.context_sha256 == item.context_sha256, ImageAnalysisJob.pass_number == 2,
        ))).all()
        job = jobs[0] if jobs else None
        result[item.attachment_id] = {
            "image_analysis_state": item.state,
            "semantic_types": item.reading.semantic_types if item.reading else [],
            "content_signature": digest(item.reading.model_dump(mode="json")) if item.reading else None,
            "pixel_sha256": job.pixel_sha256 if job else None,
            "visual_hash": job.visual_hash if job else None,
        }
    return result
