# Backend

Owner: Codex.

## Explicit two-pass export probe

`python -m studgroup.two_pass_probe ARCHIVE PRIVATE_REPORT --as-of ISO_TIMESTAMP`
screens every text message in the seven days ending at an explicitly zoned timestamp.
DeepSeek selects even weak academic signals; a second extraction pass sees merged
eight-message neighborhoods and reply relatives. Split contexts overlap by three
messages. Output cites source IDs and dates, preserves date-window metadata, and
retains private signal excerpts for materials/timetable review. Media/OCR is not
analyzed. No lexical filter removes messages before the first model pass.

This is an explicit test tool, not the live processor or an automatic publication
path. It never writes production data, supplies no unverified timetable, and does
not silently create next-lesson dates. Conservative reservations and actual peak
usage are saved before/after each call with a maximum separate run budget of $0.08
(the owner-approved test ceiling). Prior diagnostic reservations must be deducted
from this ceiling when resuming the current test across separate reports.
The CLI automatically waits and resumes retryable transport failures at the failed
fragment, with 30/60/120/240/300-second capped backoff. `--once` stops after one
iteration for diagnostics; `--once --resume` explicitly continues a saved report.
Budget exhaustion, invalid output and permanent rejection require intervention.
Resumption preserves reservations and completed batches. Reports are atomically
replaced, successful provider responses are checkpointed before further processing,
and a local file lock prevents parallel requests against the same report. Reopening
a complete report is free; changed archives, time scopes or fragment plans are
rejected. After restarting the command, it resumes rather than starts over. There
is no claim of provider-side exactly-once execution: an ambiguous request without
a saved response may need retrying and keeps its original reservation.
Invalid screening IDs escalate the
whole supplied batch instead of silently discarding possible signals.
Reports contain private chat excerpts: keep them in ignored `tmp/` with mode 0600.
Review duplicate/overlapping candidates before any production publication. The
live budget ledger and live processing policy are not modified by this probe.

## Provider reservation settlement

Each request reserves its maximum cost before sending. A confirmed pre-processing
rejection (HTTP 400/401/402/422/429) or connection/pool failure before request
delivery releases only that request's reservation. Read/write failures, HTTP 5xx,
truncated/malformed successful responses without valid usage and ambiguous cancellation retain their
reservation until usage can be reconciled. An error code alone never establishes
zero usage; the provider adapter supplies explicit evidence of releasability.
The live processor settles both attempt and global totals under the reservation
lock, including superseded attempts. Daily totals derive from settled attempts.
Both export probes use the same error metadata and preserve earlier actual costs.
No historic reservations are refunded automatically and budgets are not increased.

## Private owner incident alerts

Set `OWNER_TELEGRAM_USER_ID` to the owner's positive personal Telegram user ID.
`BOT_ADMIN_USER_ID` does not implicitly configure alerts. The owner must have
started the bot and not blocked it. Provider/validation/configuration/budget errors
create durable SQL incidents; an active code is notified once, then notified again
on recovery or a new incident. Successful provider processing closes provider error
codes, including credentials/output errors. Unexpected embedded-pipeline errors
are reported as a safe generic code, never as source text or an exception body.

Delivery targets only the configured personal owner, never the group or a headman
broadcast. Negative/group IDs are refused. Pending rows are locked while sending;
Telegram failures keep the alert pending for another delivery attempt. Delivery is
at-least-once if Telegram accepts a send but its acknowledgement or DB commit is
lost. A sleeping/down web process cannot deliver until it resumes; if PostgreSQL
is unavailable, a new incident cannot be persisted until connectivity returns.
The standalone local export probe is not the hosted incident sender.

## Owner-only testing update feed

`OWNER_UPDATE_NOTIFICATIONS_ENABLED=true` enables personal bot notices for persisted
live academic changes. Set `OWNER_UPDATE_NOTIFICATIONS_SINCE` to an explicit
timezone-aware activation timestamp; no old-history replay is inferred. The same
positive `OWNER_TELEGRAM_USER_ID` is the only recipient. Published AI notices,
headman audit changes (including description, confirmation, materials, schedule,
conversion, cancellation and undo), and new review candidates are covered. Candidate
messages explicitly say they require review, not that a real card was published.
Baseline imports stay quiet and local probe reports are not published events.

The existing bot outbox persists and deduplicates each source event. Already queued
sources are excluded so bursts beyond one batch drain after restart. Telegram errors
keep testing notices pending; acknowledgement loss still has at-least-once semantics.
Disabling the flag supersedes pending testing notices and does not disable incident
alerts, student inboxes or normal bot panels. Nothing is broadcast to students/groups.
Mini-app links use only a configured HTTPS origin. This temporary feed does not
change AI scheduling, budget limits or the normal product notification preferences.

### Analysis lifecycle reports during testing

Migration 0016 adds nullable finish/outcome/metrics fields to AI attempts without
replaying history or changing the frontend contract. The same owner-only testing
flag queues a start and an end report for every live target-message attempt. One
attempt is one target message plus its context window, not a whole-chat scan or an
unimplemented two-model cascade. Separate local-filter start/end reports cover
new sources classified irrelevant without a provider request; idle polling never
fabricates a run. Reports are independent of `/admin` and `/st` panels.

Reports include group timezone timestamps, attempt duration and elapsed job time,
target/context/submitted message counts, validated context-window count, distinct
cited source-fragment count, extracted proposals, applied proposals/source counts,
created/updated/unchanged cards, review proposals, and outcome/error. Card mutation
counters are captured inside publication, not guessed from total database size.
Every retry retains the same job identity and gets its own attempt report and
job-wide known-token/cost/reservation totals. Expired leases generate interrupted
attempt reports; late responses can reconcile previously uncertain spend.

Use returned usage even when the model output is invalid/truncated. Dollar figures
are estimates from the saved provider tariff, not a provider invoice. If usage is
unknown, explicitly show unknown tokens/spend and the held reservation separately;
confirmed pre-processing rejection shows zero spend. Successful screening/parse
is not proof of successful publication: review candidates are not counted as used
cards. There is no product cap on the owner's report count, no digest batching or
working-hour restriction on delivery; Telegram rate/availability and queue order
still apply. AI working hours and USD execution budgets remain unchanged. Local
unpublished export probes are not live hosted jobs and do not generate these bot
reports; their own private JSON reports preserve returned failure usage too.

## Activity-driven AI schedule

With `AI_SCHEDULE_ENABLED=true`, the embedded/external processor consumes live text
at fixed half-hour cutoffs (07:00, 07:30, …, 22:30) in `AI_SCHEDULE_TIMEZONE`
(Europe/Moscow). In the 23:00–24:00 extension, a group needs a signal within the
last 30 minutes; cutoff runs at 23:00/23:30. Text arriving after the last cutoff
waits for 07:00. Webhook receipt timestamps and activity are committed with inbox
changes. Service/media activity can signal the group, but does not itself invoke
the text provider. Duplicate deliveries/no-op edits do not trigger fresh work.

Only retained, unprocessed live versions are eligible; imported history never
wakes this schedule. Catch-up after a sleeping free Render instance is one current
cutoff, not a replay of every missed slot. Quiet groups produce no AI calls. A cycle
may process several relevant messages using the existing target-message extraction;
it is not a guarantee of one provider request for the entire group. Transport
recovery resumes only unfinished source versions, even in the same slot, after
30/60/120/240/300-second backoff. Transient outages do not exhaust a two-attempt
ceiling; invalid-output retries retain their limit. Expired SQL leases can be
reclaimed after restart. Historical transport jobs abandoned by the old two-attempt
policy are reopened only for current retained source revisions in active pilot
groups, not completed tasks or permanent errors. Budgets, working hours and the
late-activity gate still apply. Requests stop at the daily cutoff.
The existing daily/global USD limits and usage ledger remain unchanged. Production
also needs `AI_ENABLED=true`, and any old absolute `AI_ENABLED_UNTIL` must be cleared
explicitly when replacing a one-time test window with this recurring policy.

## Private command panels

`/admin` and `/st` each own a separate durable Telegram message. Navigation edits
that message; callbacks select their own panel and subsequent typed inputs follow
the selected mode, not whichever session happens to be awaiting text first.
Repeating a root command removes only that mode's tracked previous command,
inputs, panel and temporary controls, and supersedes its unsent screens. `/start`
(including invitations), independent alerts and digests are never part of cleanup.
Native user-picker reply keyboards use temporary tracked messages because Telegram
cannot attach them through editMessageText. Apply migration 0014 before startup.
If a tracked bot message cannot be deleted, its inline buttons are disabled.
Messages from before tracking was deployed cannot be reconstructed from Bot API
history and are not indiscriminately deleted. Deletion is subject to Telegram limits;
delivery remains at-least-once across ambiguous network failures.

## Headman cabinet /st (contract 0.8.0)

Private Telegram command `/st` lists only the caller's active pilot groups where
their membership role is headman or deputy. Every action rechecks membership;
commands never promote users. The owner explicitly assigns roles through `/admin`.
Apply migration 0013 before startup. Existing backend outbox delivery (configured
with `BOT_ADMIN_USER_ID`) runs independently of AI and delivers cabinet replies.

The root menu has separate Homework and Control Points buttons. Each has its own
active/archive list, type-scoped search/pagination and creation button; question review
remains shared. Available: question review and deferral, manual homework/control-point creation,
editing, confirmation, cancellation, conversion, merging, retained source excerpts,
forwarded-message drafts with original timestamps, HTTPS material links, revision
protected card/schedule undo, single-occurrence and explicitly confirmed recurring
schedule edits, online links, student-access suspension/restoration, one-time 24h
invitations (1–10 per batch; Telegram membership verification required), group
summary, timezone and opt-in personal question times. Material links and schedule
changes are projected into the WebApp; files/OCR are not part of this release.

Known pilot limitations accepted for publishing: participant/settings actions are
not in the correction journal; undo of a card created from an AI candidate does not
restore the candidate to review; owner role assignment has no role-removal button.
Question delivery uses the existing web process, so a sleeping free Render instance
catches up on waking; it is not a guaranteed always-on scheduler. Questions are off
by default; no production role or notification preference is enabled automatically.

## Profile, inbox and private bot administration (contract 0.7.0)

The production header reads the authenticated session, not demo data. Its profile
offers a light/dark switch (sun/moon). A non-sensitive appearance cookie remembers
the choice on this device; before the first choice, the theme follows Telegram.
No credentials or learning data are stored in that cookie. The group-scoped
`GET /v1/notifications` inbox persists personal read receipts via
`POST /v1/notifications/read-all`. Changed homework and academic events generate
deduplicated notifications; the first historical import does not. This is an in-app
change inbox, not scheduled Telegram reminders. Inbox visibility lasts 90 days.

Set backend-only `BOT_ADMIN_USER_ID` to the platform owner's Telegram ID. In the
owner's private bot chat, `/admin` offers active pilot groups, then manual numeric ID
or Telegram's native user picker, followed by explicit confirmation. New memberships
are ordinary students; existing roles are preserved. Telegram cannot enumerate all
group members through this picker. Sessions expire after 15 minutes; stale callbacks
are rejected. Membership changes and audit records commit with the received update;
durable outbox replies retry independently of AI availability. Telegram delivery is
at-least-once, while update handling is deduplicated. Apply migration 0010 before use.
Configure the webhook for message, edited_message and callback_query updates with
`TELEGRAM_WEBHOOK_SECRET`; never put bot credentials in the frontend.

This application will contain:

- Telegram bot update ingestion;
- domain and application services;
- AI classification and extraction pipeline;
- persistence and background jobs;
- notifications;
- subscription, payment, and referral logic;
- HTTP API consumed by the WebApp.

The first implemented foundation uses Python and FastAPI. Current executable
features: `/health`, dependency-aware `/ready`, exact-origin CORS, persistent
Telegram session bootstrap and group-scoped `/v1/schedule`. Apply database
migrations with `uv run alembic upgrade head` before startup. Schedule patterns
and activated memberships currently require provisioning; Telegram onboarding,
extraction remain in progress. Telegram webhook text ingestion and homework
read/completion endpoints are now implemented. Configure TELEGRAM_WEBHOOK_SECRET
and deliver updates to /v1/telegram/webhook with Telegram's secret header. Delivery
markers and raw text commit together; pending messages are durable in PostgreSQL.
The extraction worker is not yet implemented. Cancellation
and reschedule overrides are not yet exposed.

The DeepSeek text adapter is implemented in `studgroup.ai`. Backend-only `.env`
settings: `DEEPSEEK_API_KEY`, optional `DEEPSEEK_BASE_URL` (official HTTPS endpoint
only), `DEEPSEEK_MODEL` (default `deepseek-flash`). The key uses `SecretStr` and
must not be sent to the frontend. `httpx` is a runtime dependency.
The economical first pass accepts at most 12,000 input characters and 1,400 output
tokens, disables thinking, and makes exactly one HTTP request with a 45-second
timeout and no redirects. Response bodies and credentials are not logged.
Strict output validation rejects missing/extra fields, invalid confidence,
timezone-less deadlines and non-midnight date-only deadlines in the group timezone.
Original message time anchors relative dates. Unknown facts remain null; confidence
below 85 requires further context; non-urgent incomplete proposals stay hidden.
This adapter alone does **not** publish cards, run a queue or persist token usage.
The reasoning cascade, durable jobs/usage, cost limits and owner incident alerts
must be connected before unattended production processing is enabled.

`GET /v1/homework` implements active Tasks and Archive, including personal completion,
with all/today/week/mine/archive filters and session-bound cursor pagination
(limit 1–100, default 50). Cancelled and past-deadline items appear only in Archive;
rows and detail access are preserved. Date-only deadlines remain active through
the group calendar day. Migration
0005 adds the independent cancellation timestamp. Cursors expire after 15 minutes;
restart loading after a completion mutation. Contract: 0.4.0.

Run from `apps/backend`:

```sh
uv sync --dev --frozen
uv run uvicorn studgroup.main:app --reload
uv run pytest
uv run ruff check .
```

Start local PostgreSQL and Redis from the repository root with
`docker compose -f infra/compose.yaml up -d`. Default connection settings use
localhost. Set `WEBAPP_ORIGIN` to the actual frontend origin. No Telegram or AI
credentials are needed for unit tests. `/ready` returns 503 until dependencies
are reachable; `/health` remains a process liveness check.
# Local preview: deadline refresh and one test week

To update the existing isolated review database without reseeding or losing personal marks:

```sh
python -m studgroup.local_preview ../../tmp/real-preview '/Users/a1111/Desktop/чаты/ChatExport_БИ 1.2.zip' --refresh-deadlines --repeat-next-week
```

Run from `apps/backend` with its virtual environment. The repeat flag explicitly copies
only 5–11 October to 12–18 October 2026 in this test dataset; reruns do not duplicate
lessons. It is not an approved production timetable. Relative deadlines are recomputed
from source timestamps, and missing deadlines use the next subject lesson only within
known coverage. Historical missing weeks remain unknown. No additional DeepSeek call is
made. Existing personal completion is preserved; changed deadlines increment revisions.
# Reviewed archive and control points

The isolated test can apply private evidence decisions with:

```sh
python -m studgroup.review_import ../../tmp/real-preview '/Users/a1111/Desktop/чаты/ChatExport_БИ 1.2.zip'
```

It reads the ignored `review-decisions.json`, validates referenced source IDs against
the original export and performs no provider calls. Homework and academic deadlines
are stored separately. Important approximate dates remain hints, not fabricated exact
timestamps. Media-only messages cannot be resolved when the exported attachments are
absent. Production requires migration 0007; Base metadata creation here is limited
to the explicit isolated preview setup, not a production migration substitute.
