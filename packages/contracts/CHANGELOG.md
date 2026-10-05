# Contract changelog

## 0.2.1 — 2026-10-05

- Added nullable next_lesson to TodayResponse, selected by backend server time.
- Added populated, empty and unresolved-week schedule examples.
- Added schedule.read to the active session example.

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
