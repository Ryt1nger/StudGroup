# ADR-011: Published student-card projection API

Status: accepted

Date: 2026-10-14

## Context

The Mini App needs C+ card type, evidence sources, attachment metadata, deadline certainty and
student-safe changes. Canonical items also contain shadow and review-only state that must never
become public merely because a read endpoint knows how to render it.

## Decision

- `CanonicalProjectionLink` is the publication boundary for enriched student responses.
- Student APIs enrich only an existing published `Homework` or `AcademicDeadline` row and only
  from the canonical revision recorded by that link. They never create or promote a projection.
- Public card kinds use the canonical enum: `homework`, `test`, `control_point`, `assessment`,
  `credit`, `exam`, and `other_academic_deadline`.
- `confirmed_subject` is nullable. Legacy subject fields remain for older clients; a missing
  confirmed subject is shown as absent rather than inferred.
- Deadline certainty and origin are separate from whole-card verification. Unknown deadlines
  remain null and are not synthesized.
- `sources` contains access-scoped Telegram actions. Attachments expose safe metadata and their
  source-message URL, never Telegram file identifiers.
- `updates` contains only a sanitized summary of the currently projected published revision.
- `/v1/subjects` is derived from active-group published data and timetable subjects; it does not
  read canonical shadow candidates.

## Consequences

The frontend receives one versioned, backward-compatible response shape while shadow execution
remains isolated. Inline attachment preview and a complete public revision timeline require later
contracts. Removing legacy response fields requires a separate compatibility decision.
