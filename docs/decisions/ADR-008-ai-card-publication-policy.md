# ADR-008: AI card editing and publication policy

Status: accepted

Date: 2026-10-12

## Context

The product owner wants AI to act as an intelligent academic editor, not as a rigid field
extractor that sends most useful results to the headman. At the same time, invented dates,
numbers, pages or actions are prohibited.

## Decision

- The product has two card destinations: tasks and deadlines. Test, credit, exam, control
  point and similar meanings are expressed in the generated title and description.
- AI may interpret connected context, split one long message into multiple cards and merge
  evidence across related messages.
- AI-generated semantic titles and structured descriptions are the primary path. A factual
  deterministic fallback may repair one display field when the other field still makes the task
  actionable. A new card whose title cannot be made specific or whose description has no
  supported actionable content is held for review; neutral text such as “clarify from chat” is
  never a student-facing card.
- Automatic publication is the default. Numeric confidence alone never blocks it.
- Minor uncertainty publishes with a headman-only `needs_attention` reason. Serious
  conflict, material ambiguity, explicit model inability to choose or a human lock holds
  the card for review.
- Partial but actionable cards are student-visible; unknown optional fields stay unknown.
- Service hashtags (`#орг`, `#важно` and equivalents) suppress timetable-only subject
  inference. An explicit recognised subject hashtag or source-backed model subject may still set
  the subject. A clock coincidence with the lesson cannot assign the subject or its next-lesson
  deadline to an organisational post.
- A short burst of messages/files forwarded by a headman or deputy is exposed to extraction as a
  medium-strength `staff_burst`. The model must merge only semantically continuing parts; time
  proximity alone never authorises a merge.
- A reliable next lesson of the identified subject supplies a `schedule_inferred` homework
  date. Students see neutral next-lesson wording; only the headman sees its unconfirmed basis.
- Explicit subgroup assignments become separate cards. For configured split subjects, recognised
  cards are visible only to the selected subgroup; uncertain subgroup cards stay visible to all and
  require editor review
  group members. Membership-based filtering is deferred.

## Consequences

Evaluation must measure factual consistency, completeness, overmerge/oversplit and headman
review load—not only field accuracy. The backend validates critical facts and preserves
source links without turning every missing optional field into rejection.

The publication gate distinguishes missing optional metadata from missing task meaning. A card
may lack a subject or date, but it may not reach students with both its action and requirements
replaced by placeholders. Updates remain compatible: if a new update has unusable text, the
resolver keeps the previously accepted title/description instead of holding or degrading the
existing card.
