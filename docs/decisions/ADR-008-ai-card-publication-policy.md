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
  deterministic fallback is allowed only when display text is missing, malformed or fails
  evidence validation.
- Automatic publication is the default. Numeric confidence alone never blocks it.
- Minor uncertainty publishes with a headman-only `needs_attention` reason. Serious
  conflict, material ambiguity, explicit model inability to choose or a human lock holds
  the card for review.
- Partial but actionable cards are student-visible; unknown optional fields stay unknown.
- A reliable next lesson of the identified subject supplies a `schedule_inferred` homework
  date. Students see neutral next-lesson wording; only the headman sees its unconfirmed basis.
- Explicit subgroup assignments become separate labelled cards, initially visible to all
  group members. Membership-based filtering is deferred.

## Consequences

Evaluation must measure factual consistency, completeness, overmerge/oversplit and headman
review load—not only field accuracy. The backend validates critical facts and preserves
source links without turning every missing optional field into rejection.
