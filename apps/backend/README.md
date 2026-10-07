# Backend

Owner: Codex.

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
