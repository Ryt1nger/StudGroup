# MVP implementation boundaries for Modules 2–15

Status: approved

Date: 2026-10-04

Final product authority: Ryt1nger

## 1. Purpose

This document records the minimum decisions required to begin implementation without fully designing every remaining module. Module 0 remains the source of product behavior, Module 1 remains the target technical foundation, and implementation proceeds through vertical slices.

Details not listed here are deliberately deferred until the corresponding slice is about to be implemented. A deferred detail must not be invented in code when it changes product behavior; it must return to the product owner for a decision.

## 2. Approved blocking decisions

### D1. Group connection

- The headman adds the bot to the Telegram group and grants the required minimal administrator access.
- The headman runs `/connect`.
- The backend verifies that the caller is a group administrator before creating the StudGroup group and assigning the headman role.

### D2. Student activation

- The headman generates a batch of unique one-time invitation codes or links.
- Every invitation belongs to one StudGroup group, expires after 24 hours, can be revoked, and is consumed after one successful activation.
- Activation also requires backend verification that the Telegram user is currently a member of the source group.

### D3. Existing group history

- StudGroup does not connect a headman's personal Telegram account and does not use a user-authorized MTProto session.
- During onboarding, the headman may upload a Telegram Desktop export for the current semester.
- The last seven days are analysed completely with conversational context.
- Older imported history receives a low-cost first pass. Deeper analysis is limited to still-relevant long-term items such as control work, tests, credits, exams, projects, presentations, long homework deadlines, active schedule patterns, permanent schedule changes, and materials connected to future events.
- Only active or future academic facts become cards.
- Imported evidence is visibly marked as imported. A direct Telegram source link is optional because exports may not preserve a usable deep link.
- Retention uses the original message timestamp. Imported raw data whose retention period has already expired is removed after the required extraction and validation work.

### D4. Telegram permissions

- The bot must be an administrator so membership verification is reliable.
- Only the minimum required rights are requested.
- StudGroup does not delete messages, ban users, change group information, appoint administrators, or publish as the group.
- Connection fails with an actionable explanation when required permissions are missing.

### D5. First frontend scope

- The first frontend delivery is a production-quality Telegram WebApp shell with light and dark themes, navigation, loading, empty, and error states.
- The Today screen, homework card, source display, and source action are connected to live backend data.
- Other approved screens appear as coherent prepared sections but remain explicitly unavailable until their slices are implemented.
- Backend domain design accounts for the data required by all approved screens.
- A screen-to-data matrix documents future needs, but OpenAPI exposes only implemented behavior. Placeholder endpoints are forbidden.

### D6. AI integration

- DeepSeek is the only live AI provider in the initial MVP.
- All provider calls pass through one internal `AIProvider` boundary.
- Business services do not call provider HTTP APIs directly.
- Tests use a deterministic fake provider and do not spend AI tokens.
- The boundary centralizes structured output validation, prompts, reasoning levels, timeouts, retries, token and cost accounting, and error mapping.
- A second live provider and automatic failover are deferred.

### D7. DeepSeek outage behavior

- Telegram data is committed to PostgreSQL before AI work begins.
- Failed AI jobs retry with bounded exponential backoff and remain recoverable.
- An older job can never overwrite a newer message version or confirmed human revision.
- A confirmed provider incident triggers an immediate private Telegram alert to the product owner in the system-administrator bot mode.
- Identical errors are grouped into one incident instead of generating one alert per job.
- Long incidents produce controlled progress updates, and recovery always produces a recovery notification.
- Authentication failure, exhausted balance or quota, queue growth, and permanent job failure have distinct alert reasons.
- Technical alerts are never sent to students, headmen, or deputies.

### D8. Closed pilot access

- During the pilot, `/connect` succeeds only for an allowlisted group or with a valid pilot authorization code issued by the product owner.
- Ordinary student invitations do not bypass this group-level gate.
- The gate is configuration-driven and can be disabled after the pilot without changing membership or group data.

### D9. Slice 1 student UI semantics

- The Slice 1 student feed contains homework cards only. Prepared schedule,
  subjects and AI sections remain visibly unavailable until their own slices.
- Chips and actions without implemented behavior are hidden. This includes the
  mockup-only `Практика`, `Добавлено преподавателем`, calendar and `⋯` controls.
- Red styling and the `Срочно` label are driven only by the backend `urgency`
  field. The frontend must not derive urgency from deadline proximity.
- `Подтверждено` means an explicit confirmation by the headman or deputy.
  Information published from ordinary group conversation is labelled
  `Из сообщения группы`.
- The personal completion action is named `Выполнено мной`, supports undo and
  uses revision protection. A materially changed card may reappear after it was
  completed and must say `Изменено после выполнения`.
- Students never see numeric confidence values or internal revision numbers.
- The full source is shown on the detail screen. Lists may use the compact labels
  `Из группы` and `Импортировано`. An unavailable source is a normal retention or
  access state, not a generic application error.
- The WebApp follows the Telegram theme automatically and has no manual theme
  switch in Slice 1.

## 3. Remaining module boundaries

| Module | Implement now | Deliberately defer |
| --- | --- | --- |
| 2. Telegram Bot Core | `/connect`, role bootstrap, one-time student invitations, membership verification, WebApp launch | deputy management UI and non-critical service commands |
| 3. Message ingestion | text updates, edits, replies, topics, idempotency, durable persistence | albums and broad media handling until the media slice |
| 4. Domain core | Group, User, Membership, RawMessage, Event, Evidence, revision and processing state; schema must remain extensible for all approved screens | payment and referral tables until their slices unless a compatibility field is essential |
| 5. AI pipeline | classification and homework extraction through `AIProvider`, strict structured output, cost accounting | full event taxonomy and multi-provider fallback |
| 6. Group profile | minimum subject identity and aliases required for homework | automatic long-term profile learning |
| 7. Schedule and materials | data structures and screen-to-data mapping only | live schedule, personal schedule, images and PDFs until later slices |
| 8. WebApp | complete shell and live Today screen | live data for other screens |
| 9. Notifications | data model and idempotency fields only | user-facing delivery until the notification slice |
| 10. Headman tools | correction contract and revision protection in slice 2 | full moderation interface and digest queue |
| 11. Payments | preserve group-owned subscription boundary | T-Bank integration until product trust and staging exist |
| 12. Referrals | preserve attribution invariants | referral UI, ledger and payouts until payments exist |
| 13. Security | Telegram verification, least privilege, secret handling, ownership checks, safe logs | expanded compliance work before live payments |
| 14. Metrics | ingestion health, queue age, extraction quality and AI cost | large dashboard stack |
| 15. Testing and pilot | tests for the active vertical slice and closed-pilot gate | full 500–1000 example evaluation set until ingestion and taxonomy stabilize |

## 4. Development start gate

Implementation may begin when:

- Module 0 and Module 1 remain canonical and linked;
- these eight decisions are represented in acceptance tests or implementation tasks;
- the [screen-to-data matrix](screen-data-matrix.md) and first OpenAPI slice are reviewed with the frontend owner;
- Telegram, DeepSeek, database, and deployment secrets are available through approved secret storage rather than Git.

The remaining modules do not require separate detailed specifications before the first vertical slice starts.
