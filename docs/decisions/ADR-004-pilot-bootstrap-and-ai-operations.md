# ADR-004: Pilot bootstrap and AI operations

Status: accepted

Date: 2026-10-04

## Owner update — 2026-10-10: structure-first group bootstrap

Every authorized group follows the durable states `mapping -> backfill -> live`.
Before the first paid request, build and persist a local map from known Telegram
topic IDs/names, message-to-topic membership, reply edges, schedule subjects and
hashtags. If none form a reliable hierarchy, persist `unstructured` and continue
with chronology, replies and text. Topic names and the map may guide routing and subject interpretation,
but are not evidence for a task, date or deadline. A newly observed or renamed
topic refreshes the map before later provider work without replaying completed
history.

After the initial map, analyse only messages at or after the persisted moment when
the bot joined the group, under a new analysis generation. Uploaded archive rows
from before that boundary may inform the free deterministic structure map, but are
excluded from paid targets and model context. The Telegram join service event is
authoritative; for an older installation that did not retain it, the first message
actually received by the bot is the conservative boundary. Old terminal jobs remain
as an audit trail but cannot suppress the new generation. The backfill uses the same
light-then-deep order, global single-flight lock, checkpoints, retry/circuit rules,
hourly working window and budget ceilings as live processing. Failures leave the
unfinished source/stage pending; they do not restart completed work. Only after no
generation target remains pending does the group enter `live` and return to the
normal activity-triggered hourly cadence.

The existing Telegram HTML import did not contain topic service metadata, so exact
historic topic IDs cannot be reconstructed for those rows. The map records that
limitation and uses replies, hashtags and schedule subjects for old history; all
future Bot API messages persist `message_thread_id` and observed topic names.

Acceptance: mapping makes no provider call; a generation-1 terminal job does not
block generation 2; sources before `bot_added_at` never join a paid snapshot or
model context; imported/expired sources at or after that boundary may join it;
topic metadata and the compact map reach both passes as non-evidentiary context;
restart resumes the same generation; and a topic rename refreshes the map without
starting another full-history replay.

## Owner update — 2026-10-10: begin protected cost measurement

Re-enable hosted DeepSeek processing for the cost measurement. Keep the hourly
activity gate, global single-flight execution, resumable checkpoints, validation,
automatic circuit breaker, USD ceilings and owner budget notifications active.
The health endpoint must distinguish enabled, disabled, expired and emergency-stop
states without exposing credentials. Outside the explicitly approved
structure-first bootstrap, activation does not replay imported history or bypass
the fixed-package rules.

## Owner update — 2026-10-10: serialized resumable AI runs

Keep hosted AI emergency-stopped while replacing the polling cascade with one
durable state machine. A fresh group run requires both an hourly cutoff and an
activity signal newer than the previous package. A group may have only one runnable
package: all targets in its fixed snapshot complete the light screen before any
signalled target enters deep extraction. New arrivals wait for the next package.
Across all groups and rolling deployments, at most one provider request may be in
flight. Do not start a request with less than 50 seconds left in its processing
window.

A transient transport/API failure freezes that package, preserves completed light
and deep checkpoints, sleeps with capped backoff, and resumes only the unfinished
stage. Authentication, balance and provider-configuration failures sleep until the
next hourly boundary. Validate deep proposals independently: remove unknown citation
IDs only when verified evidence remains, reject an uncited/malformed proposal without
discarding valid siblings, and never manufacture target evidence. Every normalization
and rejection remains visible in attempt telemetry and the aggregate run report. An
unusable top-level response is terminal and is never repeated with the same prompt.
Light-screen citation formatting is normalized locally because screening cannot
publish facts.

Three consecutive unusable quality failures pause the remaining package until the
next hourly boundary. A schema-valid empty deep result is a normal correction of a
high-recall screen false positive: record its tokens and zero output, complete that
target, and continue. Hourly/daily/total ceilings still bound false-positive spend.
Transport failures retain the shorter configured circuit cooldown. A valid response after a
real provider failure closes only that failure's incident, not every historical
error code. Budgets, per-stage retry caps, leases, source-version idempotency and
the emergency production kill switch remain independent final safeguards.

Acceptance: two targets screen-screen-deep-deep; a retrying target blocks the rest
of its group and any later package; restart resumes its saved stage; three invalid
deep results prevent a fourth call until the next hour; a malformed sibling cannot
discard a valid proposal; diagnostics remain owner-visible without alert flapping;
quality failures make one paid attempt; concurrent workers produce one global
provider call; valid empty results do not open an error incident; and
near-cutoff work remains pending without a reservation.

## Owner update — 2026-10-09: hourly scheduled runs

Change new-target scheduling from half-hour packages to hourly packages at 07:00,
08:00, …, 23:00 Moscow time. New text arriving after a cutoff waits for the next
hour. The final 23:00 run still requires group activity within the preceding 30
minutes; later text waits until 07:00. Durable recovery of an already-started job
may continue inside its original slot after backoff. This cadence change does not
reenable the emergency-stopped hosted AI processor.

Acceptance: 07:05 text is ineligible at 07:30 and eligible at 08:00; quiet groups
make no calls; imported history does not wake the schedule; there is no 23:30 run.

## Owner update — 2026-10-09: seven-day cost measurement with runaway protection

Measure the full-quality two-pass live pipeline for seven normal study days before
optimizing it. Raise the live group/day ceiling to USD 1 and the experiment-wide
ceiling to USD 15. Keep screening every retained live text/caption and deep analysis
for signals; cost measurement must not obtain a cheaper result by silently reducing
quality. Separate the existing backlog from steady-state daily cohorts in the final
analysis. Report known provider usage separately from conservative unknown-use
reservations.

Budget increases do not mean unbounded execution. A rolling USD 0.25 per-group/hour
safety ceiling limits spend velocity while allowing ordinary hourly packages to
finish. Three consecutive provider/paid-output failures open one persistent global
circuit for five minutes. Its cooldown expires automatically, permits a probe, and
a valid response closes the incident and continues the durable queue. Isolated
failures do not open the circuit. One source stage may make at most eight spend-risk
recovery attempts; exhausting it isolates that source rather than blocking other
messages. Existing unique source-version jobs, SQL leases, stage checkpoints,
request reservations, daily/total ceilings and owner-only incident delivery remain.

Acceptance: duplicate sweeps cannot create paid duplicates; one transient failure
pauses its active group package without losing checkpoints; three consecutive
spend-risk failures prevent a fourth call during cooldown; processing resumes
without manual action after cooldown; a valid
probe clears the circuit; the hourly ceiling rolls forward automatically; and one
poisoned source cannot retry forever or consume the whole experiment budget.

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
minutes. Recovery may retry in the same hourly slot, but does not bypass live
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

## Owner update — 2026-10-09: joining online lessons from the app

Capture HTTPS joining URLs from group text/captions and explicit reply context,
independently of the paid analysis budget. Store source/revision-scoped links in
0020, never fetch destinations or log room passwords. Recognize supported meeting
hosts and RANEPA BigBlueButton; do not confuse PDFs, quizzes or attendance QR links
with joining URLs. Bind unambiguous subject/date/time evidence to one occurrence.
An unlabeled URL can use a uniquely running remote lesson or one starting within
30 minutes of the original message, not processing time. Explicit permanent-link
wording applies only to matching remote timetable patterns, forward from its source.
Distinct rooms in one post need semantic mapping rather than arbitrary selection.

Deep analysis also returns separate private online_lesson proposals, with literal
source URL validation and confidence gates. URLs are not homework. Preserve manual
URLs and locked schedule decisions; compare original source/edit time rather than
analysis completion time when selecting replacements. Edits/removed captions and
source retention revoke automatic links. Telegram message deletions unavailable to
the bot are not inferred. Backfill retained existing meeting posts without new model
calls. Ambiguous links stay stored but do not generate guessed buttons.

Use existing optional LessonOccurrence.online_url in Schedule/Today/detail; no new
response fields. Add a conditional protected external joining action to schedule
and lesson detail, through Telegram openLink on click when available, otherwise a
normal HTTPS anchor. Do not change Today layout. Hide joining for ended/cancelled
lessons. SDO authentication remains the student's own external-platform login.

Acceptance: single-use URLs do not repeat next week; explicit permanent culture
link appears only for its remote lectures; old processing cannot replace a newer
source URL; edits/expiry/manual locks and group scope prevent stale leakage; source
URL query tokens are preserved; the user can open it from the app without an
automatic external navigation.

## Operator correction — 2026-10-09: publish saved evidence, not only code

The saved export probe was not applied to the cloud, despite working live processing.
Provide a private, explicitly invoked reviewed-publication CLI (no public endpoint,
provider call, new user identity, enrollment or automatic proposal approval). Bind
the payload to the configured owner and an existing active authorized membership;
verify its hash/size and original source text/timestamps against live edits. Insert
missing retained export sources through the common inbox and publish via existing
manual-lock and notification rules. Record outcomes in deterministic jobs/candidates
so restarting the one-time invocation cannot duplicate cards. Only individually
evidence-reviewed entries can override a low model confidence; ambiguous discussions
stay in review. Past deadlines remain past, visible through archive policy.

For this owner-approved pilot, repeat only the existing all-week patterns of
5–11 October through 18 October, as previously requested; do not create invented
lessons. Re-resolve undated homework after the original source time and normalize
the linear-algebra subject against its longer timetable name. Reuse a unique
source-linked baseline event when its reviewed import key differs, rather than
duplicate it. Verify active homework with the same SQL predicate as the API, not
by claiming success from total rows. Use temporary Render private variables for
the operation, clear them afterwards. Normal startup optionally consumes only an
explicit private payload; without it this operation is a no-op. Logs contain
counts/card IDs/deadlines only, never chat bodies or secrets. Contracts unchanged.

## Owner update — 2026-10-10: uncertain complete proposals remain useful

For the live pipeline, a concrete assignment with subject, title and description is
published even when model confidence is below 85. Preserve the model facts and mark
the stored homework as `verification_state=needs_clarification`; only an active
headman or deputy sees that review marker. A student sees the same group card with
the ordinary `from_group_message` verification value. A response that contains no
publishable facts remains a private review candidate and never becomes an empty card.

The batch prompt must not request `kind=needs_context`: uncertain proposals use a
concrete kind and lower confidence, while non-tasks are omitted. The adapter still
retains an unexpected legacy `needs_context` response as a review candidate instead
of silently discarding it. Repeated target analysis with an overlapping, tightly
clustered set of cited source-message IDs anchors to the same source card so a
hashtag/header and its continuation do not create duplicates.

## Owner update — 2026-10-09: one report pair per scheduled run

A planned run is a fixed group/hourly package, not an individual source or model
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
the legacy keyword relevance gate in this mode. Weak signals with identifiable
academic evidence escalate to deep extraction; uncertainty by itself is not a
signal, and only the model decides that a target is chatter.
The neighborhood includes up to eight messages on either side, constrained by the
existing byte/message bounds; selected historical context is evidence, not a new
target or full-history replay. Binary OCR/file-content parsing is not added here.

Each stage is a separately reserved/settled/reported attempt. Migration 0018 adds
the nullable SQL screening checkpoint per source revision. A successful screen
commits before deep work. Every target in a scheduled package finishes screening
before any deep call. Deep recovery reuses the checkpoint and may continue in the
same hourly slot; new targets still wait for their own slot. Transport stages retain
bounded recovery without reusing lease generations; validated quality failures are
not repeated with the same prompt. An edited source
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
