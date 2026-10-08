# ADR-004: Pilot bootstrap and AI operations

Status: accepted

Date: 2026-10-04

## Owner update — 2026-10-08: request reservation settlement

Release a request's reservation only when the adapter confirms rejection before
processing (HTTP 400/401/402/422/429), a connect/pool failure before delivery, or
a local preflight failure before any provider call. Ambiguous read/write failures,
5xx errors, malformed/truncated successful responses and in-flight cancellation
retain reservations. Retryability is independent of billing certainty. Error
bodies and credentials are never logged. A successful response settles to actual
reported token usage. Live settlement updates the attempt and global ledger under
the singleton budget lock, independently of stale job fencing; daily aggregates
then reflect the settled attempt. Explicit export probes follow the same evidence
policy. Existing historical reservations require separate evidence-based review;
this change does not reset ledgers, change live budgets, or replay paid requests.

Acceptance: confirmed rejection frees capacity; timeout cannot erase possible
spend; releasing a failed deep-pass reservation preserves the successful screen
charge; compatibility error codes and retry rules remain stable.

## Owner update — 2026-10-06: provisional homework deadlines

For homework whose deadline cannot be resolved, use the start of the next scheduled
lesson of the same subject. If the original message is outside timetable coverage,
use the next lesson from the current server time as a provisional pilot assumption.
Do not present this as a date stated in the chat: verification remains inferred.
Never invent a lesson when the subject or timetable is unavailable. Explicit source
information takes precedence on re-extraction. Filling missing dates must not change
already known dates, personal completion marks, or repeatedly slide the saved deadline.
This default applies to homework, not independently dated control points or assessments.

## Context

Implementation needs stable decisions for Telegram onboarding, existing group history, frontend scope, AI provider boundaries, outage handling, and pilot access. Fully designing Modules 2–15 before coding would delay the first validated product journey.

The Telegram Bot API does not let a bot retrieve arbitrary history from before it joined the group. Telegram documents `messages.getHistory` as a user-only method. Membership lookup for other users is guaranteed through `getChatMember` when the bot is a chat administrator.

## Options

- Fully specify every remaining module before implementation.
- Start coding with implicit assumptions and decide behavior inside implementation.
- Approve only cross-module blocking decisions, preserve explicit boundaries, and defer non-blocking detail.

## Decision

Approve the eight decisions in `docs/product/mvp-implementation-boundaries.md` and begin the first vertical slice.

Use Telegram Bot API only for continuous group operation. Bootstrap prior context through an explicit Telegram Desktop history import, not through the headman's personal Telegram session.

Use DeepSeek behind an internal provider boundary. Persist source data before AI execution, retry failures safely, and notify the product owner immediately through a private system-administrator bot mode when a provider incident is confirmed.

Keep the pilot closed through a configurable owner-controlled group allowlist or pilot authorization code.

## Consequences

- Development begins without fourteen additional detailed module-design phases.
- Existing groups can recover relevant semester context without handling a user's Telegram credentials.
- The bot requires minimal administrator status in every connected group.
- Backend schema planning covers all approved screens while API implementation remains incremental.
- DeepSeek failure delays extraction but does not lose Telegram data.
- The product owner receives operational alerts without exposing technical incidents to ordinary users.
- Deferred choices remain explicit and cannot silently become product rules through code.

## References

### Implementation checkpoint (2026-10-05)

The backend provider boundary uses a strict JSON-only text adapter with an
explicit original-message timestamp and group timezone. Chat content is untrusted
input, not executable instructions. Keys are backend-only; remote error bodies
are not logged. HTTP redirects and arbitrary provider hosts are prohibited.
The economical first pass has explicit input/output limits and no automatic
retries; it is not the full reasoning cascade. Only one-homework proposals are
supported by the single-message interface; explicit КТ/control points, assessments
and tests now have separate extraction kinds and independent deadline persistence.
The explicit import batch can return several sourced proposals. Cross-message
corrections still require evidence review, not invented homework cards.
SQL job leases, budget reservations, stale-attempt fencing and deduplicated incident
records are implemented and regression-tested as of 2026-10-06. The Celery/Redis
deployment and actual private alert delivery are still unverified. Schema-valid
AI output is not itself proof that its extracted facts are correct.

Deadline resolution now follows Module 0: relative dates use original source timestamps;
missing homework deadlines fall back to the next subject lesson only with valid calendar
coverage. Deterministic common relative-date checks override a misdated model proposal.
Ambiguous stated dates block timetable fallback. The isolated preview can explicitly copy
one supplied week into the next week; stable IDs make this operation idempotent. Updating
preview deadlines preserves personal marks, records a significant revision and does not
publish to production or make additional paid model requests.

The local archive review scanned 3,322 message records and inspected academic text
and reply context. Its reviewed decisions live in the ignored private preview
directory; private chat contents are not committed as source code. Exact deadlines,
date windows and approximate/unresolved date hints are preserved distinctly.
Control-point reclassification hides the old homework representation without
deleting evidence or personal marks. The SQL processing core now has a PostgreSQL
concurrency test; hosting, live onboarding and broker deployment remain unfinished.
The local review is not autonomous live AI. Paid re-evaluation and error analysis
are deferred at the owner's request until after the infrastructure work.

- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [DeepSeek thinking controls](https://api-docs.deepseek.com/guides/thinking_mode/)

- [Telegram Bot API: getChatMember](https://core.telegram.org/bots/api#getchatmember)
- [Telegram API: messages.getHistory](https://core.telegram.org/method/messages.getHistory)
