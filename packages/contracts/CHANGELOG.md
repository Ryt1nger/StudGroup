# Contract changelog

## 0.10.0 — 2026-10-19

- Added the headman/deputy cabinet API under `/staff` (tag `Staff`): timetable view with editor
  revisions, add/change/cancel lessons, days without lessons, group timezone and first-week
  anchor (headman only), the "deadline needs the headman" queue, set/confirm a card deadline and
  per-field locks.
- Added permissions `schedule.write`, `deadline.manage` (headman and deputy) and
  `group.settings.write` (headman). Students keep the four existing permissions.
- Added error code `action_rejected` (409) for actions refused by current data or a group rule.
- Added nullable `Deadline.label` (homework) and `AcademicDeadline.deadline_label` with the neutral
  student wording for uncertain deadlines.
- A deadline set or confirmed by the headman/deputy is reported as certainty `confirmed`,
  origin `manual`.

## 0.9.0 — 2026-10-14

- Added student-safe published-card metadata to homework: canonical `card_kind`, nullable
  `confirmed_subject`, deadline `certainty`/`origin`, current-revision `sources[]`, typed
  materials and sanitized `updates[]`.
- Kept `type`, legacy `subject` and `source_detail` unchanged for older Mini App clients.
- Expanded academic-deadline kinds to the canonical backend enum; only `homework` is projected
  to the homework endpoints and supports personal completion.
- Added `GET /subjects`, derived from active-group published rows and the timetable. It never
  reads shadow or review tables.

## 0.2.2 — 2026-10-05

- Owner-approved Today behavior: due_today precedes overdue; expired deadlines remain visible for strictly less than 24 hours after the deadline, then disappear from all Today sections.
- Storage and direct homework reads are unchanged. No response fields changed.

## 0.2.1 — 2026-10-05

- Added nullable next_lesson to TodayResponse, selected by backend server time.
- Added populated, empty and unresolved-week schedule examples.
- Added schedule.read to the active session example.
- Aligned example location labels; location is display-ready text without client-added prefixes.

## 0.2.0 — 2026-10-05

- Added group schedule read API with explicit week anchor and unresolved parity state.
- Added schedule.read permission and invalid_request error; frontend must regenerate its client.
- Existing endpoint fields are preserved. Cancellation and reschedule overrides are still being implemented.

## 0.1.1 — 2026-10-04

- Removed Today pagination from Slice 1, eliminating cross-page section merging.
- Clarified that personal completion does not change group status or revision.
- Restricted source actions to backend-built `https://t.me/` URLs.
- Clarified that CORS trusts the deployed WebApp origin, not Telegram as an origin.
- Added renderable 200-response examples for session, Today and homework details.

## 0.1.0 — 2026-10-04

- Added the initial Slice 1 contract for Telegram session bootstrap, Today,
  homework detail, personal completion and membership recheck.
- Fixed opaque bearer-token rules and a 12-hour session lifetime.
- Added optimistic concurrency and the `revision_conflict` error.
- Made `urgency` the sole source of urgent UI styling.
- Kept invitations bot-only and omitted placeholder APIs for prepared screens.
