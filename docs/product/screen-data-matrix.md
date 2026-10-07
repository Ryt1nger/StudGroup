# WebApp screen-to-data matrix

Status: approved planning baseline

Date: 2026-10-04

## 1. Purpose

This matrix ensures that the backend domain model can support every approved WebApp screen without implementing speculative API endpoints. It describes required data and ownership; the versioned OpenAPI contract is added slice by slice.

`Live in slice 1` means real backend integration is required now. `Prepared` means the WebApp shell may expose the section with an honest unavailable state, while the domain model preserves the necessary relationships.

## 2. Primary screens

| Screen | Required data | Main domain ownership | Initial status |
| --- | --- | --- | --- |
| Today | next class, important tasks, changes, source, confidence/status, update time | Event, ScheduleEntry, Evidence, EventRevision | Live in slice 1 for homework; other blocks may be empty |
| Tomorrow | classes, deadlines, control work, preparation material | Event, ScheduleEntry, Material, Evidence | Prepared |
| Tasks | task type, subject, deadline, urgency, confirmation, personal completion | Event, Subject, UserEventState | Prepared |
| Task detail | description, materials, source, status, revision history, personal completion | Event, Evidence, Attachment, EventRevision, UserEventState | Live in slice 1 only for the selected homework card |
| Schedule | group/personal mode, week pattern, start/end time, room/link, teacher, changes | GroupSchedule, PersonalSchedule, ScheduleEntry, EventRevision | Prepared |
| Subjects | subject identity, aliases, nearest meaningful event, task/material counts | Subject, SubjectAlias, Event, Material | Prepared |
| Subject overview | next class, current tasks, recent changes, material summary | Subject, Event, ScheduleEntry, Material | Prepared |
| Subject materials | type, file/link, size, date, source, related event/class, availability | Material, Attachment, Evidence | Prepared |
| What's new | event revision, added/changed distinction, read state, event date | EventRevision, UserFeedState, Evidence | Prepared |
| Search and AI | constrained group-data query, cited results, freshness, conflicts | SearchIndex or query service, Event, Material, Evidence | Deferred; no general personal AI chat |

## 3. Supporting screens and states

| Screen or state | Required data | Ownership | Initial status |
| --- | --- | --- | --- |
| Group connection | Telegram chat, headman identity, required permissions, pilot authorization | Group, Membership, PilotAuthorization | Live in slice 1 |
| Student activation | one-time invitation, Telegram membership, active group constraint | GroupInvite, Membership | Live in slice 1 |
| History import | import job, source file, original timestamps, progress, discovered candidates | HistoryImport, RawMessage, Evidence, EventCandidate | Onboarding extension before external pilot |
| Personal schedule upload | file/text input, extraction, semester, week mapping, confirmation | PersonalSchedule, PersonalScheduleEntry, Evidence | Prepared |
| Personal/group conflict | both variants, event horizon, student decision | ScheduleConflict, PersonalSchedule, EventRevision | Prepared |
| Notification centre | real homework/event changes, personal read state, related entity | Notification, NotificationRead | Live in contract 0.7.0; scheduled Telegram reminders remain out of scope |
| Profile and settings | authenticated name, Telegram ID, group and role; light/dark theme switch | User, Membership | Live profile in contract 0.7.0; appearance preference persists on this device in a non-sensitive cookie, initially follows Telegram; other editable preferences remain prepared |
| Headman tools | correction, confirmation, cancellation, actor, expected revision | Correction, EventRevision, AuditLog | Slice 2 |
| Deputy invitation | invitee identity, inviter, acceptance and role status | RoleInvitation, Membership | Prepared |
| Question digest | unresolved issue, recipient, routing, batch, answer, escalation | QuestionQueue, QuestionBatch | Prepared |
| Read-only subscription state | group subscription status, grace end, permitted operations | Subscription, GroupAccessPolicy | Prepared |
| Payment | group payer context, session, verified status, receipt and renewal | Payment, Subscription | Deferred until P2 |
| Removed/suspended member | Telegram verification status, recheck time, blocked access | Membership | Prepared |
| Empty/loading/offline/error | request state, retryability, correlation identifier where safe | API error contract and frontend state | Shell support in slice 1 |

## 4. Shared response concepts

The backend must use consistent concepts across screens:

- stable identifiers and ISO timestamps;
- group timezone plus UTC source timestamp;
- status, urgency, visibility, confidence band, and revision;
- subject summary rather than duplicated subject strings where identity exists;
- evidence/source summary with source kind and availability;
- explicit unknown fields instead of invented values;
- permissions describing available user actions;
- pagination and freshness metadata for lists;
- machine-readable error code plus safe user-facing message;
- personal state separated from group-owned academic facts.

## 5. Slice 1 contract boundary

The first OpenAPI slice exposes only what the connected Today and homework-detail experience needs:

- authenticated WebApp session bootstrap;
- current user's active group and role summary;
- Today feed containing a sourced homework card;
- homework detail with evidence and current revision;
- source action metadata;
- one-time invitation activation and `/connect` support services where HTTP boundaries are required;
- documented loading, empty, validation, permission, conflict, and service-unavailable errors.

The endpoint names and concrete schemas are defined in `packages/contracts` immediately before implementation. This matrix is not itself an API contract.
