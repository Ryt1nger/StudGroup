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

## Implementation correction — 2026-10-09: exercise numbers versus dates

An explicit decimal exercise label (e.g. `номер 5.1–5.4`) is not a date interval.
Mask only these exercise-reference spans in deterministic deadline evidence, not
the original source sent to the model. Preserve independently stated date clauses
and date windows. This fixes blocked next-lesson fallback for otherwise undated
homework without changing the original-message-time policy. Regression acceptance:
homework posted 8 October at 12:34 with those exercise numbers resolves to the
known next mathematics lesson, 9 October at 10:40; a separately stated 10–15 October
submission window remains ambiguous, not a single inferred deadline.

## Owner update — 2026-10-09: one report pair per scheduled run

A planned run is a fixed group/half-hour package, not an individual source or model
stage. Migration 0019 adds a durable run envelope with all eligible source-revision
keys (including beyond the worker's 50-source claim page). Attach attempts to this
run in their internal metrics. Emit one start and one aggregate finish; stage
completion, deep continuation and transport retries must not emit new report pairs.
Replies arriving beyond the package cutoff belong to the next run. Silence emits
no run. A retry reuses its original open run across slots/restarts; the final is
queued only after all package targets finish, fail permanently or become obsolete.
Budget-paused/unavailable targets stay pending; existing incident notices explain
the interruption instead of fabricating a completed package. Queued card changes,
review findings and provider incident alerts remain independent and owner-only.

Aggregate known tokens/cost over all attached attempts, keep unknown reserves
separate, count successful screen/deep targets distinctly from request attempts,
and deduplicate important/used source IDs. Preserve internal per-attempt history.
Suppress only undelivered legacy run/filter stage notices in the outbox and at
delivery (to handle deployment overlap); do not delete sent Telegram messages,
incident alerts, card notifications or analysis data. Non-scheduled diagnostic
attempts keep their existing individual reports. No frontend contract change.

Acceptance: two targets and four successful stages produce exactly two run notices;
retry/restart does not produce a second start; pending tail delays the final;
51 targets still produce one pair; fresh arrivals wait for their next slot.

## Owner update — 2026-10-09: semantic two-pass live processing

Enable `AI_LIVE_TWO_PASS=true` in the live pilot. Every new/edited retained text
message (including an attachment caption) enters DeepSeek's high-recall semantic
screen with bounded neighboring messages and reply/subject evidence. Do not use
the legacy keyword relevance gate in this mode. Weak signals and uncertainty
escalate to deep extraction; only the model can decide that a target is chatter.
The neighborhood includes up to eight messages on either side, constrained by the
existing byte/message bounds; selected historical context is evidence, not a new
target or full-history replay. Binary OCR/file-content parsing is not added here.

Each stage is a separately reserved/settled/reported attempt. Migration 0018 adds
the nullable SQL screening checkpoint per source revision. A successful screen
commits before the deep call. Deep recovery reuses it and may continue in the same
half-hour slot; new targets still wait for their own slot. Each stage retains its
own validation retry allowance without reusing lease generations. An edited source
gets a new version job and screening. Published cards commit per deep result under
the existing confidence/manual-lock/multiple-task rules. No UI contract changes.

Migration requeues only seven-day retained live messages marked completed without
any AI job in an authorized group. It does not replay successful/failed provider
jobs or imports. This corrects old local-filter loss without a semester-history run.
The same bounded repair runs under the claim budget lock in the new processor, so
an overlapping old instance cannot permanently discard the migration's backlog.
Once any model job exists, successful or failed, this repair will not reopen it.
Owner reports explicitly label screen/deep stages and signals; same approved hours,
daily/total spend limits, reservation rules and owner-only delivery continue. A
budget block leaves targets pending; this change does not promise unlimited throughput.

Acceptance: keyword-free `матан 16` is screened with surrounding evidence; chatter
also consumes a real light call, not a fabricated zero-cost report; deep transport
failure resumes without another screen; stage continuation does not wait for the
next slot; edits invalidate checkpoints; captions enter the same durable inbox.

## Owner update — 2026-10-09: incremental export publication

Do not wait for the end of an export to apply successful analysis. The explicit
two-pass export runner checkpoints every validated deep fragment before delivery.
With `--publish-group GROUP_UUID`, deliver each ready fragment into the configured
database through the normal publication policy, in a separate committed transaction.
The source messages must already exist in that authorized active group and match
the original text/timestamps and retention. This option never creates memberships
or silently chooses a group. Without it the runner remains a non-publishing probe.

Use deterministic fragment job IDs and database transactions so delivery retry
does not duplicate cards or candidates. A database failure queues saved delivery,
not another paid analysis call. Provider failure leaves only unfinished deep work
pending. Existing saved reports can be explicitly delivered without paying to
reanalyze them. Preserve confidence gates, manual locks, owner change notifications
and one-task-per-source safeguards: review is distinct from unfinished analysis.
This is the export runner's private checkpoint/supervisor, not a new hosted export
upload endpoint or background queue; current live message processing already commits
per target. No contracts/schema change and no historical proposal auto-approval.

Acceptance: first fragment is delivered while the second fails; retry reuses the
first response; lost database acknowledgement is idempotent; stale/missing source
rolls back the entire fragment; no provider call is needed to deliver saved results.

## Owner update — 2026-10-09: submission clock interpretation

Extract the submission hour independently of the calendar day. In the academic
submission context, `до 12.00 в четверг` means Thursday noon with a timed deadline,
not date-only. A plain named weekday on the source weekday means that same day,
even after expiry; explicit next-week wording retains its separate policy.
Bare academic `до 4` defaults to 16:00 and is marked inferred. Explicit morning,
night, afternoon/evening qualifiers and full 24-hour colon clocks take precedence.
Calendar dates, exercise references and quantity limits are not clocks. A bare
submission hour without a named day uses the original source day, marked inferred,
never processing time. Explicit clocks are not rebound to the end of a lesson.

Acceptance: a Wednesday message naming Thursday 12.00 resolves to Thursday noon;
Thursday's same-day wording does not roll forward a week; 04:00 and `4 утра`
remain 04:00; elapsed timed homework is selected into the archive by the existing
timestamp filter. This parser/prompt change applies on extraction/re-extraction;
it does not silently overwrite previously stored or manually confirmed cards.

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
