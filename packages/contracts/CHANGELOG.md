# Contract changelog

## 0.15.0 — 2026-10-23

- Added `POST /notifications/{notification_id}/read`: marks one notification of the caller's group
  read for the caller and returns the updated inbox (404 for an unknown or foreign id). Additive.
- Added optional `urgency` (`normal` | `urgent`) to `EditHomeworkRequest`: the headman and deputy can
  set or clear the «Срочно» mark when editing a card. Additive; omitted means unchanged.
- Added `GET`/`PATCH /profile`: the caller's own name, e-mail, study-platform data (platform, https
  URL, login, https calendar URL) and notification preferences. The study-platform password is
  write-only (`sdu_password`, cleared with `clear_sdu_password`) and is never returned, only
  `sdu.password_set`; the server stores it encrypted and answers 503 when it has no key. Additive.

## 0.14.0 — 2026-10-23

- Added `review` to `listHomework` `filter` and a `review` query flag to `listAcademicDeadlines`: the
  editors' queue of active cards/events flagged `needs_clarification`. Headman and deputy only;
  students get 403 `permission_denied`. `review` cannot be combined with `archive` on deadlines (422).
- Added optional `review_count` to the homework list and the deadlines list responses, present only
  for the headman and deputy, so a client shows the review tab only while something needs a look.
- Clarified (no behaviour change): a card whose known deadline has passed is archived regardless of
  personal completion, so a completed overdue card appears only under `filter=archive`.

## 0.13.0 — 2026-10-22

- Added `GET /attachments/{attachment_id}/image`: the photo of a card, streamed from Telegram for
  members of the owning group. Added optional `MaterialLink.replaces_description` (default false):
  when true on a `kind: photo` material the client shows that image instead of the text
  description (the description is still sent as a text fallback). Additive; nothing else changed.

## 0.12.0 — 2026-10-21

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

## 0.11.0 — 2026-10-21

- Files that stay in Telegram: a card material with `kind` other than `link` may now carry an
  API-relative signed `url` (`/v1/files/{id}?exp=..&sig=..`, valid 6 hours, no Authorization header).
  Clients must resolve it against the API base URL. Absolute https links are unchanged.
- Added `GET /homework/{id}/file-link` and `GET /deadlines/{id}/file-link` (headman/deputy only): a
  `t.me` deep link where the editor sends the file to the bot.

## 0.10.0 — 2026-10-21

- Added `PATCH /homework/{id}` and `PATCH /deadlines/{id}`: a headman or deputy edits title,
  description, subject, deadline (and the event kind / date hint of an academic event) from the
  Mini App. Same rules as the Telegram bot: `expected_revision` guard (409 `revision_conflict`),
  audit and undo data, AI lock; students receive 403 `permission_denied`.
- Added the `InvalidRequest` (422) response and the `homework.edit` / `homework.material.write`
  permissions. The card `permissions` array now contains them for a headman/deputy; the academic-event
  detail gained an optional `permissions` array. Both are UI hints, the server re-checks every request.

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
