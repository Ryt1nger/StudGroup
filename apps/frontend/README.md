# Frontend

Owner: Claude. Core v2 events/calendar integration explicitly assigned to Codex by the product owner on 27 October 2026.

Current onboarding and Slice 1 requirements: [`docs/workflow/claude-frontend-stage-0-brief.md`](../../docs/workflow/claude-frontend-stage-0-brief.md).

Run: `pnpm install` then `pnpm --filter @studgroup/frontend dev:mock` (see `FRONTEND_ARCHITECTURE.md` §16).

This application contains the Telegram WebApp and consume versioned contracts from `packages/contracts`.

Current published-card coverage and the proposed compatibility boundary are documented in [`CARD_READINESS.md`](./CARD_READINESS.md).

Expected product areas include:

- Today, Tomorrow, Week, and Deadlines;
- schedule and personal schedule;
- subjects, materials, and event details;
- notification and profile settings;
- headman and deputy management views where approved.

The approved architecture requires a TypeScript client generated from OpenAPI. Claude must review and document the concrete frontend stack during Stage 0 before scaffolding the application.

## Core v2 events integration

`/events` lists future approved group events and retained history. `/events/:eventId` shows details;
`/events/review` and `/events/review/:eventId` use the existing headman/deputy backend review guards.
The current Today day view and Schedule week strip render the same IDs from `/group-events`.
Moving/cancelling an event invalidates every event interval and detail query; focus/reconnect and
reopening the application refresh saved structured data. No event read or manual action invokes AI.

Navigation and queries follow optional per-group session capabilities. Absent flags keep the old
four-tab interface; editors can review drafts independently of student publication. Editor input
and its expected revision survive background refreshes; conflicts require an explicit reload.
The existing theme/surface primitives, Telegram source opening and transient query cache are used.
Use the development-only `?scenario=events` journey for UI verification. It is a mock API, not proof
of Telegram ingestion, production persistence or a real pilot. Production bundles exclude mocks.

`/schedule/review` provides editor confirmation of recognized timetable rows: source, effective
dates, subject, subgroup, week parity, teacher/room, existing occurrence mapping and once/recurring
scope. It uses the current staff schedule and group-local subgroup metadata. Missing dates are
left blank. Stale official schedules require explicit re-review; source expiry blocks confirmation.
The development-only `?scenario=schedule-review` exercises this UI against a mock API.
