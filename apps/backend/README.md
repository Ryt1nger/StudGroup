# Backend

Owner: Codex.

## Private read-only shadow wave export

To export stored C+ results without another provider call, set `DATABASE_URL` through the
normal secret mechanism and run:

```bash
uv run python -m studgroup.export_shadow_wave \
  --wave wave-a-spread-1 \
  --output tmp/private/studgroup_cplus_wave_a.json
```

The exporter only reads stored shadow rows. The existing preview path uses a savepoint that is
always rolled back. The resulting JSON contains private Telegram text and must remain under the
Git-ignored `tmp/` directory; do not attach it to issues, commits, CI artifacts, or application
logs. Use the bundle only with an explicitly approved offline review workflow.

The bundle distinguishes stored validation, the current deterministic `effective_validation`,
card-editor output before cross-run merging, and the final owner preview. Raw provider responses
are explicitly marked unavailable because successful shadow responses were not retained.

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
on recovery or a new incident. Hourly, per-group daily and experiment-wide budget
limits have distinct alerts. Their first alert includes the group key, measured spend
and configured limit; repeated five-second queue checks do not create notification
spam. Successful provider processing closes provider error
codes, including credentials/output errors. Unexpected embedded-pipeline errors
are reported as a safe generic code, never as source text or an exception body.

Migration 0017 preserves numbered incident episodes rather than overwriting the
history of each error code. Alerts include the episode number, original detection
time and last manifestation, recurrence count, recovery time and notification-send
time, formatted to seconds in Moscow time. Repeats of an ongoing incident retain
its number; a recurrence after recovery receives a new number. API failures,
model-output validation failures and budget limits are distinguished explicitly.
Old records retain their saved timestamps and are labelled historical (their
earlier repeat count is unknown); delayed first delivery is labelled backlog.
Already delivered legacy alerts are not resent just to assign numbers. Start and
recovery have separate durable pending flags so fast recovery cannot erase the
original error. Run reports refer to the same incident number. Safe HTTP status or
exception class may be included; never raw exception/provider text.

Delivery targets only the configured personal owner, never the group or a headman
broadcast. Negative/group IDs are refused. Pending rows are locked while sending;
Telegram failures keep the alert pending for another delivery attempt. Delivery is
at-least-once if Telegram accepts a send but its acknowledgement or DB commit is
lost. A sleeping/down web process cannot deliver until it resumes; if PostgreSQL
is unavailable, a new incident cannot be persisted until connectivity returns.
The standalone local export probe is not the hosted incident sender.
Each alert delivery reacquires its own locked episode in a non-expiring session;
committing one alert cannot invalidate the next alert and cause MissingGreenlet.

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

When owner testing is enabled, startup logs an `AI_STARTUP_DIAGNOSTICS` snapshot
with feature flags, key-presence only, usage/reservations, live/imported/pending
message counts, activity timestamps, card/review counts and next-request budget
gates. This performs no provider calls or database writes and exposes no message
text, personal identities or credentials. It is best effort with a five-second
timeout and must not prevent application startup.

With `AI_SCHEDULE_ENABLED=true`, the embedded/external processor consumes live text
at fixed hourly cutoffs (07:00, 08:00, …, 22:00) in `AI_SCHEDULE_TIMEZONE`
(Europe/Moscow). The final 23:00 extension requires a group signal within the
last 30 minutes. Text arriving after the last cutoff
waits for 07:00. Webhook receipt timestamps and activity are committed with inbox
changes. Service/media activity can signal the group, but does not itself invoke
the text provider. Duplicate deliveries/no-op edits do not trigger fresh work.

Only retained, unprocessed live versions are eligible; imported history never
wakes this schedule. Catch-up after a sleeping free Render instance is one current
cutoff, not a replay of every missed slot. Quiet groups produce no AI calls. One
active package per group completes all light screens before any deep extraction;
new arrivals cannot overtake it. Provider calls are globally single-flight across
groups and rolling deployments. Transport recovery freezes the active group and
resumes only its unfinished source stage, even in the same slot, after
30/60/120/240/300-second backoff. Transient outages do not exhaust a two-attempt
ceiling. Authentication, balance and configuration failures sleep to the next hour.
Unknown citation IDs are removed only when verified evidence remains; malformed,
uncited or unverified proposals are rejected individually without discarding valid
siblings. Every repair/rejection remains visible in attempt telemetry and the final
run report. An unusable top-level response is terminal and is never paid twice with
the same prompt. Three consecutive unusable or empty deep responses pause the
remaining package until the next hour. Expired
SQL leases can be reclaimed after restart. Historical transport jobs abandoned by
the old two-attempt policy are reopened only for current retained source revisions
in active pilot groups, not completed tasks or permanent errors. Budgets, working
hours and the late-activity gate still apply. A request cannot start within 50
seconds of a configured or daily cutoff.
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

## Public info commands and landing deep links

`studgroup.bot_info` answers read-only info topics in any private chat, for any user,
before panel routing. No state, no authorization decisions.

| Topic | Command | Deep link (landing) |
| --- | --- | --- |
| Tariffs ("coming soon", no prices yet) | `/tariffs` | `https://t.me/studgroup_rf_bot?start=tariffs` |
| How to use | `/help`, `/howto` | `?start=howto` (or `help`) |
| Connect a group | `/connect` | `?start=connect` |
| FAQ | `/faq` | `?start=faq` |
| Support | `/support` | `?start=support` |

`/start invite_<code>`, bare `/start`, `/st`, `/admin` and all other commands are unchanged;
an unknown `/start <payload>` falls through to the previous behaviour. `/connect` here is a
private-chat info text only; the headman group `/connect` from the domain rules is not
implemented by it. Optional `SUPPORT_CONTACT` (for example `@name`) is shown by `/support`
and `/connect`; when unset the bot says contacts will appear soon. Update the topic texts in
`bot_info.render` when tariffs are approved.

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

## Private /start walkthrough (migration 0036)

`/start` opens a button-only panel (`bot_start.py`, callback prefix `t`) built on the same
durable panel mechanism as `/st` and `/admin`: one message edited in place, every new `/start`
removes the previous screen and the person's own messages, buttons go stale with their screen.
Student path: welcome -> «Я студент» -> «Ввести код» -> paste a code -> «Готово» with the app
button. A wrong typed code keeps the panel waiting for another try. A person who is already
connected sees their group and the app button instead of the role question. Info topics
(how it works, FAQ, support) are buttons on the welcome screen; the old commands still work.

Invitation codes are secrets: the message carrying one (typed code or `/start invite_<code>`)
is deleted at once. Set `BOT_USERNAME` (without `@`) so the headman's «Создать приглашения» shows
tap-to-join links `https://t.me/<bot>?start=invite_<code>`; unset, it shows the old command text.

### Connecting a new group (`bot_connect.py`)

Adding the bot to a Telegram group is enough: the bot creates the group and its environment
(group row with `pilot_authorized`, bot-added boundary now, timezone Europe/Moscow until changed
in the cabinet, AI profile) and starts at once, with no approval, and **the person who added the
bot becomes the group's single headman**. The bot never writes in the group; the new headman gets
the «группа подключена» screen in private chat (it is only delivered if they ever opened the bot,
and it warns when the bot is not a chat administrator and therefore cannot see messages).

The adder is named by `my_chat_member` or by the service message "X invited the bot"; they are
made headman unless Telegram positively says they are only an ordinary member (if Telegram cannot
say, they are trusted). `/start` typed in the group is a fallback for when those updates are not
delivered and requires a confirmed administrator. The administrator list is read with
`getChatAdministrators`, which works before the bot is made an administrator.

If nobody qualified (an ordinary member added the bot, or the adder already belongs to another
group) the group exists with «староста не выбран»; the owner's `/admin` list marks it and any
administrator of that chat can take the role later on the «Я староста» screen (it lists only
headman-less groups the person administers) or with `/start` in the group.

Guards: a group that already has a headman is never changed; one person belongs to one group; the
role is assigned under a lock on the group row, so two people cannot both win; names of groups the
person does not administer are never shown. The webhook must deliver `my_chat_member` (part of
Telegram's default update set). The owner's `/admin` assignment also refuses a second active
headman in one group.

Cost note: every group the bot is added to now starts AI analysis, and several owner tools
(`/explain`, `/shadowstatus`, the first-run preview) still assume exactly one authorized group.

### Headman cabinet by buttons

Headmen and deputies see «Кабинет старосты» on the start screen (and after connecting a group).
It opens the same cabinet `/st` opens, straight into the main menu for the usual single group:
the start screen is removed and the cabinet takes its place; «‹ В начало» in the cabinet menu
returns the other way. Cabinet texts no longer ask people to type commands; the `/st` command
still works as a shortcut. A stale button or expired session now re-opens a fresh menu or says
to use the latest message. Text input steps (titles, dates, links) remain typed by nature.

Not built yet: the promo-code step from the flow diagram (no partner model to record the code).
There is no database-level unique index on one active headman per group; add it once production
data is checked for duplicates.

## Cabinet API /v1/staff (contract 0.10.0)

HTTP entry point to the same headman/deputy rules for the Mini App cabinet. Apply
migration 0035 (`groups.schedule_version`) before startup. Every route re-reads and
locks the membership; students get 403. Deputies can edit lessons, days without lessons
and card deadlines; timezone and first-week anchor are headman-only, as in `/st`.

Every group timetable change from the bot, the cabinet, undo or reviewed publication goes
through `schedule_audit.schedule_changed`: one `schedule_changes` row, a new
`schedule_version`, then `rebuild_after_schedule_change` in the same transaction. Setting
or confirming a card deadline locks the `deadline` field and clears the deadline request;
`PUT /items/{id}/locks` replaces the locked field set. No notifications are sent.

Not in this release: schedule import, personal (per-student) schedule, rollback journal
in the cabinet, and showing days without lessons on the student schedule.

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

## Core v2 image pilot (disabled)

The durable two-pass image path is off by default. Offline tools do not import an AI provider,
HTTP client or database writer. From `apps/backend`, render only the synthetic test corpus:

```sh
uv run python -m studgroup.analysis.image_evaluation render \
  --gold tests/fixtures/ai_analysis/image-scenarios.json \
  --font /path/to/installed-unicode-font.ttf --out /tmp/studgroup-image-fixtures
uv run python -m studgroup.analysis.image_evaluation evaluate \
  --gold tests/fixtures/ai_analysis/image-scenarios.json
```

The second command deliberately reports null measurements and `UNMEASURED / AWAITING OWNER
APPROVAL` without observations. Add `--observations recording.json` to evaluate an annotated
recording; `compare --baseline baseline-recording.json --observations candidate-recording.json`
requires the same `input_sha256` (pixels AND chat/annotation snapshot). Observation format is
`analysis.image_evaluation.ObservationRun`: evidence_kind, input_sha256, per-case image states,
canonical fact annotations, types, card/display counts, schedule/event fields, optional measured
cost/latency. Matching is exact normalized annotation text; paraphrases need human adjudication.
Synthetic stub success must never be called measured model quality.

Activation requires separate owner approval. Necessary settings (none changed here):

- `IMAGE_ANALYSIS_ENABLED=true`, explicit JSON `IMAGE_ANALYSIS_GROUPS=["<group uuid>"]`,
  and timezone-aware `IMAGE_ANALYSIS_SINCE=<approved start>`. Missing start disables work.
- Begin with `IMAGE_ANALYSIS_MODE=shadow`: persist image evidence/drafts without reopening
  messages or altering published cards. Do not enable legacy photo reading for this same pilot.
- The pass reservation (default USD 0.02), image total (0.12), album/message batch (0.20), rolling
  hour (0.25), and group's existing daily C+ budget apply before calls. These are configurable
  safety reservations, not measured provider prices. Image and text accounting share group locks.
  Usage above a reservation fails explicitly and stops further work at the relevant cap.
- `IMAGE_DIAGNOSTICS_ENABLED`, `IMAGE_SCHEDULE_REVIEW_ENABLED`, `IMAGE_EVENT_REVIEW_ENABLED`,
  `IMAGE_EVENT_PUBLICATION_ENABLED` remain false. Each endpoint also requires the group allowlist;
  diagnostics/review require a freshly checked headman/deputy membership. Student events have a
  separate publication gate; personal/unknown source audiences and unmapped subgroups stay hidden.

Before an authorized shadow sample, verify both visual calls are supported by the configured
provider/model and approve its rates, source corpus, retention and monetary caps. Compare the
same rendered manifest/context with the baseline, inspect every critical number/date and every
false homework, then agree precision/recall/cost/latency thresholds. Check unknown paid outcomes,
retry counts and cost reservations in staff image diagnostics. Do not activate live processing,
student publication, historical reprocessing or a writer-mode change from these instructions alone.

Rollback is feature-only: first disable public event/schedule review gates, then image processing
and diagnostics, retaining stored sources/results. Do not drop production tables or change writer
mode automatically. Legacy published-card display remains intact; images published through the
new canonical evidence path retain their full task text. An ambiguous paid lease is held for
review rather than purchased again. Oversized combined evidence is explicitly held for review;
all completed image readings are retained, never string-truncated.

Migrations 0040–0042 are additive. Verify upgrade/downgrade/re-upgrade only on a disposable
PostgreSQL database before deployment. The draft additive API is
`packages/contracts/image-findings.openapi.yaml`; frontend adoption, event retention and subgroup
mapping require owner coordination. See the living integration report for completed gates and
`OWNER_DECISIONS_REQUIRED`.

Owner decisions 2026-10-27: approved structured events survive private-source expiry and are
retained through the event's local end date plus seven full calendar days. Drafts remain bounded
by source retention. Missing dates or a multi-day event without an end prevent publication.
Expired private-source links are unavailable; approved events never preserve copied raw chat.
Schedule confirmation accepts optional corrected_rows and explicit subgroup/lesson mapping;
AI rows remain immutable. Migration 0044 and the additive LessonOccurrence.subgroup_id field
support the pending frontend integration. No public/review flags are activated by these changes.
