# Contract compatibility

## Headman bot and manual group data (0.8.0)

Additive material-link arrays on detail/subject responses; academic-event revisions
and cancellation timestamp; optional lesson online_url. Lesson status now includes
cancelled; notification entity_type includes schedule (navigate to Schedule, not
event detail). The /st workflow is Telegram-only and permission-scoped to active
headman/deputy memberships. Owner /admin can explicitly assign roles; no automatic
promotion. Manual confirmations lock out AI overwrites, changes use revision checks
and transactional audit/outbox, invitations expire after 24h and require verified
Telegram group membership. Schedule exceptions affect one occurrence; recurring
changes require a separate explicit confirmation. Materials are links, not file/OCR
processing. No paid hosting or new provider calls are required for manual workflows.

## Homework lesson-time binding (owner decision, 2026-10-07)

No schema change. A homework deadline with a date but no time is bound to the end
of the first scheduled lesson of that subject on that date, when known. Explicit
clock times and explicit end-of-day/week windows are preserved; no matching lesson
means the existing calendar-day deadline remains. Inferred next-lesson deadlines
remain due at lesson start. Control points are unchanged. Migration 0012 repairs
retained date-only homework without AI calls or new-change announcements. Timed
homework leaves active lists at its deadline, including Today, without layout changes.

## Source-anchored homework age (owner decision, 2026-10-07)

No schema change. Undated homework uses the next known subject lesson after the
original message timestamp, never after import/processing/current time. Missing
historical timetable coverage leaves the deadline unknown. Homework without an
explicit deadline (unknown or timetable-inferred) older than seven elapsed days
from its original source moves to Archive and is omitted from Today and subject
active homework. Explicit future deadlines remain active regardless of source age.
Academic deadlines/control points are not subject to this homework age cutoff;
their existing event date/archive rules remain unchanged. Migration 0011 repairs
existing inferred dates without provider calls or fake change notifications.

## Event detail (0.6.1)

Additive GET /deadlines/{deadline_id}, operation getAcademicDeadline. Returns
generated_at, group_timezone and item (existing AcademicDeadline schema). Active
group only, retained archived events are readable; unknown/foreign/expired IDs
return 404. No homework completion endpoint is reused for assessments.
Event cards display subject as heading and title as task; click opens detail.

## Today updates section (owner decision, 2026-10-06)

new_or_changed no longer includes assignments simply because created_at is recent.
Only significant updates to already existing assignments within 24 hours qualify.
Import initialization/normalization and initial timetable fallback are not live edits.
Existing date-section priority and omission of empty sections remain unchanged.
No schema or enum change; clients still preserve server section order.

## Clarification presentation (owner decision, 2026-10-06)

No wire-format change. Homework clarification status stays unchanged in API responses.
Student UI hides the clarification badge when deadline.state=known and deadline.at
is present (including inferred timetable dates); unknown dates retain the warning.
Headman UI shows the original warning regardless of whether a date is available.
The viewer role comes from SessionContext.membership.role, not URL parameters.

## Tasks archive (0.4.0, owner-approved behavior change)

GET /homework adds filter=archive. all/today/week/mine now exclude archived items;
archive includes all cancelled and passed-deadline items within normal retention.
Cancellation archives immediately, even with unknown cancelled_at. Timed deadlines
archive at <= server time; date-only deadlines on the next group calendar day.
Today excludes archived items immediately. No completion marks or rows change.
This replaces all earlier 12-hour cancellation / 24-hour overdue display windows.
Clients must regenerate filter types and add Archive; old clients' all filter
no longer returns past/cancelled items. Cursor implementation is unchanged.

## Full Tasks list

Additive GET /homework returns HomeworkListResponse with existing HomeworkSummary
items. Requires the same active membership/session as Today and homework.read.
Filters use server time and the group's calendar; active versus archive semantics
are defined above. Personally completed active cards remain in all/mine.
Pagination order is immutable created_at then id ascending. Session-signed
cursors bind the group and filter, freeze selection time and expire in 15 minutes.
State changes are live, not a full snapshot; restart pagination after mutations.
Clients merge and deduplicate pages by id before local section grouping/sorting;
never independently render one section per page. Use generated_at for live time.
Other endpoints and completion revision semantics are unchanged.

## Cancellation timestamp

Optional nullable HomeworkSummary.cancelled_at records cancellation time, never
guessed from updated_at. It no longer controls visibility; status=cancelled is
sufficient for Archive. Stored rows, details and retention remain unchanged.

## Today personal completion clarification (owner update, 2026-10-05)

Personally completed items with a known active deadline remain eligible in their
normal Today sections with a completion indicator. Completed items without a known
deadline remain excluded. Archive rules still take precedence. Empty sections are
omitted; personal marks, details and retention remain unchanged. This replaces the
earlier rule excluding every completed card; no schema changes.

## Today deadline visibility

## Local real-data preview (development only)

An explicitly launched, loopback-only preview uses a separate SQLite database
containing owner-provided export candidates and one screenshot week. Candidates
are shown as needs_clarification for owner review, not published to a live group.
Authentication is a separate ephemeral local preview credential, not Telegram
identity verification. The normal production app has no preview login route.
Existing session/read/completion response schemas are unchanged. Imported sources
use the existing imported_history kind; absent Telegram chat IDs do not create
invented links. Nothing implies that a screenshot week repeats for the semester.

Today returns due_today/new_or_changed/upcoming and excludes archived cards as
defined above, even after recent edits. Unknown deadlines do not expire by time.
Clients preserve section order; details and retention are unchanged.

## Next lesson (0.2.1)

TodayResponse.next_lesson is an additive optional field; the backend supplies it
as null or {state: current|upcoming, lesson: LessonOccurrence}. Selection uses
server time and group timezone. Current means starts_at <= server_time < ends_at;
otherwise the earliest future lesson within the next 32 days is selected. Missing
week anchor never yields a guessed alternating lesson. Existing empty describes
homework sections only; next_lesson can be present when empty=true.

## Schedule integration (0.2.0)

`GET /v1/schedule?start=YYYY-MM-DD&end=YYYY-MM-DD` returns an inclusive range
of at most 32 days. Occurrences are ordered by starts_at and id. Week alternation
uses first_week_anchor, never ISO calendar parity. Missing anchor omits alternating
patterns and sets week_state=needs_clarification; the client must show this state.
selected_week refers to start. All timestamps include timezone offset.
Frontend regenerates the client for the new schedule.read permission and error enum.

## Versioning

- HTTP paths use the major prefix `/v1`.
- Adding an optional response field or a new endpoint is backward-compatible.
- Removing or renaming a field, changing its meaning, making an optional field
  required, or removing an enum value is breaking.
- Adding an enum value is treated as potentially breaking for generated clients
  and requires a changelog entry plus frontend coordination.
- Breaking changes require product-owner approval and a new major API prefix.

## Session token

- `POST /v1/session/bootstrap` accepts the raw Telegram Mini App `initData` in
  the `init_data` field. The backend validates its signature, bot identity and
  `auth_date`; data older than 10 minutes is rejected.
- The response contains 32 cryptographically random bytes encoded as unpadded
  base64url: exactly 43 characters matching `^[A-Za-z0-9_-]{43}$`. It is not a
  JWT and carries no client-readable claims.
- Only a SHA-256 hash of the token is stored by the backend.
- The token expires 12 hours after issue. Slice 1 has no refresh token: reopening
  the Mini App performs bootstrap again with fresh Telegram `initData`.
- The frontend sends `Authorization: Bearer <access_token>` and keeps the token
  in memory only. It must not write it to localStorage, IndexedDB, URL parameters,
  analytics, logs or error reports.
- Production CORS allows only the configured origin that actually serves the
  deployed WebApp (for the pilot, its Cloudflare Pages origin). Telegram itself
  is not added as a CORS origin. The policy allows
  `GET`, `POST`, `PUT` and the `Authorization`/`Content-Type` headers; browser
  credentials are disabled. Explicit local-development origins are configuration,
  never a production wildcard.
- Invitation expiry/consumption and a group rejected by the closed-pilot gate are
  handled in the bot flow. Session bootstrap does not return those errors: a user
  without an activated membership receives `access_state=no_active_group`.

## Concurrency

Every mutable homework representation has a monotonically increasing `revision`.
The completion mutation requires `expected_revision`. A stale write returns HTTP
409 with error code `revision_conflict`; the client refetches the resource, shows
the updated state and only then lets the user repeat the action.

The personal completion write does not increment the group-card `revision` and
does not alter its group `status`. `expected_revision` protects the user from
marking an obsolete card revision; repeating the same boolean value is idempotent.

## Today response size

Slice 1 returns the complete bounded Today feed without pagination. Backend rules
already limit `upcoming` to three cards and cards are de-duplicated across the four
sections. Pagination can be added only with an explicit section-merge contract.

## Telegram Mini App deep link

- Bot links use `startapp=hw_<homework-id-without-hyphens>`.
- `start_param` therefore matches `^hw_[0-9a-f]{32}$` and is 35 characters.
- The parameter is an untrusted navigation hint, not a credential or signature.
  The frontend restores the UUID hyphens, opens the detail route and lets the
  backend enforce session, membership and resource ownership.
- Invalid, unknown, inaccessible and expired identifiers resolve to the normal
  validation, 404, 403 and 410 states; they never bypass the Today gate.

## UI semantics fixed by the owner

- `urgency` is the only source of urgent styling. The frontend must not infer
  urgency from a deadline.
- `verification_state=manual_confirmed` is the only state labelled
  `Подтверждено`; `from_group_message` is labelled `Из сообщения группы`.
- The personal action is labelled `Выполнено мной`.
- Numeric confidence and revision values are never displayed to students.
- Source availability is a normal state. `unavailable` is not rendered as a
  generic application error.
- `source.action.url` is backend-built and, in Slice 1, may use only an HTTPS URL
  on the exact `t.me` host. Redirector and arbitrary external hosts are rejected.
# 0.4.1 — Today / Tomorrow switch

`GET /today?day=tomorrow` adds an optional, backward-compatible day selection.
Omitting `day` preserves the existing Today feed. Tomorrow contains only known
deadlines on the next group calendar day (including personal completion marks),
and that day's first scheduled lesson. Unknown deadlines are not assigned a day.
`due_today` is the existing section key for the selected day's deadlines; render
it as «Срок завтра» in tomorrow mode. Server timestamps remain actual current
timestamps, never a simulated future clock. No storage or archive rules change.
# 0.4.2 — Selected-day lesson state

Optional `TodayResponse.day_lessons_state` distinguishes `scheduled`, `finished`,
`empty` and `unknown`, computed on the server for today or tomorrow in group time.
Only a covered, resolved calendar with past scheduled lessons can be `finished`.
Today no longer presents tomorrow's lesson as today's class: the UI renders the
finished notice and leaves tomorrow's classes on the Tomorrow switch. `next_lesson`
keeps its existing shape/range for compatibility. Missing coverage is not an empty day.
# 0.5.0 — Subject page for a lesson

Adds `GET /lessons/{lesson_id}/subject`, keyed by the existing occurrence ID
(`pattern UUID:calendar date`). Authorization and subject identity come from the
active group's calendar, never client-provided subject text. The response includes
the lesson date/time, up to 100 relevant retained homework cards and total count.
Past/cancelled homework is excluded; future known deadlines after the selected
lesson day are omitted. Events/control points and materials explicitly report
`not_connected`; the frontend must not claim these collections are empty or invent
their contents. This additive route does not replace the schedule/list endpoints.
# 0.6.0 — Important deadlines are not homework

Adds `GET /deadlines?archive=false|true` and `AcademicDeadline` with separate
`control_point`, `assessment` and `test` classification. Control points never
enter homework lists or personal homework completion. Exact deadlines, explicit
date windows and approximate/unresolved `date_hint` remain distinct. A window
does not automatically become an exact submission deadline. Important is not
synonymous with urgent/red. Subject pages can now return `events_state=ready`
and a typed `events` array; `not_connected` is retained for older servers/mocks.
Migration 0007 adds independent persistence; local reclassification preserves old
source records and personal marks without destructive deletion.
# 0.7.0 — Real notification inbox

Adds authenticated active-group `GET /notifications` and `POST /notifications/read-all`.
Reads are personal and persistent, never a group-wide read marker. Historical imports
do not create fake new notifications. This is the in-app inbox for actual card changes;
scheduled Telegram reminders/digests remain outside this slice. Existing endpoints and
session/profile fields are unchanged. `/admin` is an owner-only private bot workflow,
not a frontend endpoint and does not confer headman rights on participants.
