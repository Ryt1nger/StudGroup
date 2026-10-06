# Pilot vertical delivery plan

Status: approved

Date: 2026-10-03

## 1. Delivery principle

StudGroup is implemented as complete, testable user journeys. The Module 1 stack remains the target architecture, but infrastructure features are introduced only when required by the next product slice or pilot safety.

The first slice must prove the core value chain before broadening event types or operational tooling.

## 2. Vertical slice 1: Telegram message to visible homework

### Current delivery progress (2026-10-05)

The owner accepted the Today presentation. The frontend mock screens now include
Today, homework detail, group Schedule and full Tasks. This closes the mock UI
stage, not the live vertical slice acceptance criteria below.

The next delivery stage connects real data and AI. GET /homework is implemented
with server filters and cursor pagination (contract 0.4.0); frontend integration
is handed to Claude. The strict, bounded DeepSeek text provider adapter is implemented
and unit-tested; it is not yet wired to durable processing or card publication.
Remaining: extraction worker, usage budget tracking,
deduplicated owner incidents/recovery, onboarding and a deployed group test.
Do not claim live AI or PostgreSQL integration from SQLite unit-test results.

```text
Telegram group message
        ↓
authenticated webhook ingestion
        ↓
idempotent RawMessage persistence
        ↓
background homework extraction
        ↓
Event creation with evidence and revision
        ↓
REST/OpenAPI response
        ↓
Today screen shows the card
        ↓
source action opens the original Telegram message
```

### Included

- closed-pilot group authorization;
- headman `/connect` with Telegram administrator verification;
- minimal bot administrator-permission validation;
- batched one-time student invitations and membership verification;
- text messages only;
- `homework` extraction only;
- PostgreSQL persistence for RawMessage, Event, Evidence, and processing state;
- stable idempotency for Telegram updates and extraction jobs;
- one Celery worker with conservative concurrency and Redis as broker;
- source link or source reference in the API contract;
- one read endpoint required by the frontend Today screen;
- a production-quality WebApp shell whose other approved sections expose honest unavailable states;
- a screen-to-data matrix covering all approved screens without placeholder endpoints;
- loading, empty, incomplete, and error API states;
- structured logs, health, readiness, and AI cost attribution;
- private owner alerts for confirmed DeepSeek incidents and recovery;
- unit, PostgreSQL integration, contract, and one end-to-end happy-path test.

### Explicitly excluded

- images, PDF files, and Telegram albums;
- all event types other than homework;
- student reminders;
- question digests;
- personal schedules;
- payment and referrals;
- full retention automation;
- multiple specialized worker pools;
- production-scale observability dashboards.

### Acceptance criteria

- [ ] A repeated Telegram update creates one RawMessage.
- [ ] A relevant text message produces one homework Event with evidence.
- [ ] An irrelevant message produces no published Event.
- [ ] An unknown deadline is represented as unknown, not invented.
- [ ] Repeating extraction does not duplicate the Event.
- [ ] The frontend can render the documented response without handwritten conflicting types.
- [ ] The source action identifies the original Telegram message.
- [ ] AI tokens and estimated cost are attributed to the group and extraction.
- [ ] A DeepSeek outage preserves work, retries safely, and produces one deduplicated owner incident plus recovery alert.
- [ ] A non-allowlisted group cannot complete `/connect` during the pilot.
- [ ] The end-to-end path runs locally through Docker Compose.

## 2.1 Onboarding import extension

After the live text path is stable, extend onboarding before broad external pilot use:

- accept a Telegram Desktop export for the current semester;
- analyse the last seven days completely;
- cheaply scan older history and deeply process only active long-term academic candidates;
- mark imported evidence and remove raw imported data whose retention has already expired;
- present discovered active cards to the headman for one onboarding confirmation pass.

## 3. Vertical slice 2: edits, corrections, and revision safety

- Process Telegram message edits.
- Add headman correction and confirmation.
- Enforce expected revision on AI and human writes.
- Reject a late extraction based on an older message version.
- Preserve evidence and revision history.
- Add explicit confirmed, incomplete, changed, and conflict states.

This slice is complete only after an automated race test proves that an old AI result cannot overwrite a newer human correction.

## 4. Vertical slice 3: notifications

- Schedule one homework reminder path.
- Respect group timezone and approved quiet hours.
- Use notification idempotency by user, event, revision, and kind.
- Reschedule or cancel pending notifications when an Event revision changes.
- Record terminal Telegram delivery errors.

## 5. Vertical slice 4: media

- Add image ingestion and contextual analysis first.
- Add PDF handling after image behavior is stable.
- Preserve Telegram `file_id` and store only required local or S3-compatible copies.
- Apply the approved media retention rule.

## 6. Later slices

After the first four slices pass internal-group testing, continue with:

1. remaining academic card types;
2. questions and digests;
3. personal schedules and conflicts;
4. broader retention automation;
5. subscription payments;
6. referral accounting.

The product owner may reorder these slices. Such a priority change does not modify Module 0 behavior.

## 7. Backend and frontend handoff

For each slice:

1. Codex documents the minimum API contract and compatibility note.
2. The frontend owner reviews whether the contract supports the required screen states.
3. The contract is approved before either implementation depends on it.
4. Backend and frontend implement against the same generated types.
5. The product owner accepts the complete user journey.

Only critical entities are designed contract-first in depth. Secondary presentation details may evolve pragmatically without weakening versioning or compatibility rules.
