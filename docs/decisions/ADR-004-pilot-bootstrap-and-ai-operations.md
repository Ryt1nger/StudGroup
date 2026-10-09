# ADR-004: Pilot bootstrap and AI operations

Status: accepted

Date: 2026-10-04

## Owner update — 2026-10-08: request reservation settlement

Release a request's reservation only when the adapter confirms rejection before
processing (HTTP 400/401/402/422/429), a connect/pool failure before delivery, or
a local preflight failure before any provider call. Ambiguous read/write failures,
5xx errors, malformed/truncated successful responses without valid usage and in-flight cancellation
retain reservations. Retryability is independent of billing certainty. Error
bodies and credentials are never logged. A successful response settles to actual
reported token usage. Live settlement updates the attempt and global ledger under
the singleton budget lock, independently of stale job fencing; daily aggregates
then reflect the settled attempt. Explicit export probes follow the same evidence
policy. Existing historical reservations require separate evidence-based review;
this change does not reset ledgers, change live budgets, or replay paid requests.

If a successful HTTP response includes valid usage but its model output fails
validation or is truncated, settle to that returned usage rather than keeping an
unknown-usage reservation. Preserve usage on the safe provider exception for
operational reports; never expose response/error text or credentials.

Acceptance: confirmed rejection frees capacity; timeout cannot erase possible
spend; releasing a failed deep-pass reservation preserves the successful screen
charge; compatibility error codes and retry rules remain stable.

## Owner update — 2026-10-08: resume unfinished analysis after outage

Temporary transport/API availability errors remain durable retries, not permanent
failure after two attempts. Reuse the existing SQL job/version and lease fencing;
retry only unfinished work with 30-second exponential backoff capped at five
minutes. Recovery may retry in the same half-hour slot, but does not bypass live
hours, activity gates, total/daily budgets or retention. Reopen old exhausted
transport failures only for current retained revisions; do not reopen completed,
superseded or permanently rejected work.

The export command uses an automatic recovery supervisor and atomic private
checkpoints, saved successful responses, a single-report lock, and archive/time/
fragment-plan validation. Restarting the command resumes its checkpoints. Reports
stay local and ignored; this is not a hosted export queue. A provider request whose
response was lost cannot be guaranteed exactly once without provider idempotency;
its reservation remains conservative if the failed fragment must be retried.

Acceptance: two outages followed by recovery do not replay successful screening or
deep fragments; a restart after saving a response does not pay for it again; a
completed report is idempotent; live recovery does not process newly arriving text
before its regular slot or ignore budget/time constraints.

## Owner update — 2026-10-08: private provider incident notifications

Send live DeepSeek/API/processing incidents only to `OWNER_TELEGRAM_USER_ID`,
explicitly configured separately from bot panel administration. Refuse group IDs;
never fall back to group/headman delivery. Deduplicate an ongoing incident by code,
keep pending delivery in SQL, and send a recovery notice after successful provider
processing. Include only a safe reason/code and progress/retry information, not
private chat contents, keys or remote exception bodies. Telegram delivery retries
independently of provider availability. Acknowledgement loss can produce duplicate
delivery; service/database downtime delays notifications.

## Owner update — 2026-10-08: temporary academic testing feed

During testing, opt into owner-only bot delivery of new/changed published academic
cards, manual audit operations (including materials/schedule/confirmation/undo),
and clearly labelled new AI review proposals. Use an explicit activation timestamp,
not a semester-history replay, and durable event-keyed outbox delivery. Turning off
the testing flag stops pending testing notices but preserves independent incident
alerts. Do not broadcast or enable student notifications. This feed concerns
persisted academic changes, not local unpublished probes or other students'
personal completion marks. Frontend/backend contracts stay unchanged.

## Owner update — 2026-10-08: unrestricted testing run reports

Send the owner private start/end reports for each live target-message attempt,
including retries, and for local filtering of newly discarded irrelevant sources.
Define counters explicitly: target and context messages are disjoint, one context
window is one analyzed fragment, important source fragments are unique cited
message IDs, and applied proposals/card mutations are not review candidates.
Persist finish/outcome/numeric metrics in AI attempts (nullable migration 0016),
queue messages transactionally with work, and aggregate known tokens/estimated
cost/uncertain reservations across attempts of the same source-version job.
Recover expired-attempt reports and reconcile late usage without applying stale
results. No owner-report daily/hourly or count cap; existing execution budgets,
AI schedule, Telegram constraints and at-least-once delivery semantics remain.
This is live attempt telemetry, not a claim of a full group-wide two-pass model
cascade or a hosted local-export runner. No API/contract changes are introduced.

## Owner update — 2026-10-09: identifiable incident history

Assign a persistent sequential number per incident episode. Distinguish detection,
last manifestation, recovery and notification-send timestamps to the second in
Moscow time; failures use catch time, not provider-request start time. Repeated
manifestations of an active code share one episode/count, with a new number after
recovery. Preserve opened/recovered notifications independently and link attempt
reports to the episode number. Migration 0017 adopts existing last-known records
without inventing missing historical repetitions or replaying delivered alerts.
Clearly label backlog/legacy records; older per-code history was overwritten and
cannot be reconstructed. Keep safe codes/HTTP status/exception type only.

Fix the sender's multi-alert commit-expiration failure (MissingGreenlet, confirmed
in logs at 2026-10-08 20:34:49 and 20:34:55 Moscow time) using non-expiring sessions
and per-episode lock reacquisition. This internal notification failure is distinct
from DeepSeek outages, output validation failures and budget exhaustion. Budgets,
owner-only recipients, front-end contracts and at-least-once delivery stay intact.

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
