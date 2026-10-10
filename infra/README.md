# Infrastructure

## Pilot status — 2026-10-06

Real preview data was copied insert-only from SQLite to local PostgreSQL; the
SQLite source is retained. Local API bootstrap, Today, homework and deadlines
respond successfully against PostgreSQL. That isolated preview still runs on the
owner's Mac; it is separate from the Render deployment below. No paid resources
or Telegram webhook registration were provisioned.

`compose.pilot.yaml` prepares PostgreSQL 17, Redis, API, a single Celery worker,
SQL inbox scheduler, migrations and Caddy TLS. Docker builds and the complete
broker/container stack are not yet verified (Docker is unavailable locally).
Native PostgreSQL migrations and concurrent processing tests have passed.

## Deployment

### Render pilot

The owner selected Render Free. `studgroup-db` (PostgreSQL 17, Oregon) and
`studgroup-frontend` are provisioned alongside the existing StudGroup API. The free
database expires on 2026-11-05; it is not permanent storage. After deployment the
owner explicitly approved importing their export. Actual live Telegram ingestion
remains a separate step.

Set `PROCESSING_MODE=embedded` for the free API pilot: one async SQL-inbox loop runs
inside the API, and Redis is not a readiness requirement. Shutdown cancels the loop;
committed jobs, leases and budget reservations remain in PostgreSQL. It runs only
while the Render instance is awake. `AI_ENABLED=false` remains the initial setting.
The external Celery deployment is unchanged for a later always-on worker topology.

The API accepts Render's standard `postgres://`/`postgresql://` connection URLs and
normalizes them to its asyncpg driver internally. Use the internal URL in the same
region. Do not publish credentials. The frontend uses only the public API base URL
(`https://studgroup.onrender.com/v1`), never bot or AI keys. The native build runs
`uv sync --frozen --no-dev`; startup applies migrations before Uvicorn binds `$PORT`.

Static site build: repository root; `corepack pnpm install --frozen-lockfile
&& corepack pnpm --filter @studgroup/frontend build`; publish `apps/frontend/dist`.
Do not run `corepack enable` on Render: `/usr/bin` is read-only.
The `/*` → `/index.html` Rewrite is configured. Both services reached `live`;
API `/health` and `/ready` return 200 with PostgreSQL connected. Frontend `/`,
`/today`, `/tasks` and `/deadlines` return HTML/200. CORS accepts the frontend
origin; unauthenticated homework requests return 401. The database's external
IP allowlist is empty; do not weaken it merely to query through hosted MCP.
The approved private import committed 3,065 text messages, 14 reviewed homework
records, 15 academic events and 40 schedule records. Only the explicitly supplied
Telegram account was given an active student membership; local preview users and
session tokens were not copied. Reviewed personal completion state was preserved.
Other history is retained for the generation-scoped structure-first backfill and is
not marked fully AI-analyzed until that durable generation completes.
AI was initially disabled. On 2026-10-07 the owner approved a one-time history
analysis window ending at 2026-10-08 00:00 Europe/Moscow, under the existing daily
and total budget caps. Cloud runtime logs confirm completed model processing.
After the absolute cutoff no new model requests start, including after restart;
there is no automatic next-day re-enable. Attachments without text were not uploaded
or OCR-processed.

The importer (`studgroup.group_import`) is insert-only and idempotent. The payload
was transferred via a temporary Render secret file with a checked SHA-256 digest,
not via Git or public static assets. The private file was removed after committed
counts were verified; normal API startup was restored. The database's external
access is closed. Reopen the Telegram Mini App to get a new session with membership.

### Optional portable container deployment

Required: a continuously running Docker Compose host, an API domain pointing to
it, open HTTPS ports 80/443, a hosted frontend and an off-machine backup destination.
This remains an alternative to the selected Render pilot; free VM capacity is not assumed.

Copy `.env.pilot.example` to a private `.env.pilot`, fill its placeholders and
restrict permissions with `chmod 600 .env.pilot`. Use a unique PostgreSQL password;
URL-encode it in `DATABASE_URL`. Set the real frontend origin, webhook secret,
owner Telegram ID and backend-only AI key. Never deploy the local preview factory
or its credentials publicly.

From the repository root on the selected host:

```sh
docker compose --env-file .env.pilot -f infra/compose.pilot.yaml config --quiet
docker compose --env-file .env.pilot -f infra/compose.pilot.yaml up --build -d
```

Only Caddy publishes ports; API, PostgreSQL and Redis stay internal. Configure the
Telegram webhook only after HTTPS/authentication checks. Connecting groups and
issuing invitations still require onboarding integration; preview membership is
not production authorization.

## AI safety and outstanding acceptance

The seven-day cost measurement uses three independent live safeguards: USD 1 per
group/day, USD 0.25 per group in a rolling hour, and USD 15 across the experiment
ledger. Three consecutive spend-risk failures open a five-minute automatic circuit;
one source stage is isolated after eight such recovery attempts. These controls
bound an error loop without requiring manual restart after an ordinary provider
recovery. Startup diagnostics expose limits, rolling spend and circuit state without
message text or credentials.

AI calls are disabled by default. Once explicitly enabled, SQL reserves budget
before each request, records usage and caps each source revision at two attempts.
Timeouts with unknown usage retain their full reservation. Initial caps: $0.05 per
UTC day per group and $1.70 total in this processing ledger. Other account consumers
and manual import scripts are outside this ledger; these limits do not guarantee
provider balance or extraction quality.

For an owner-approved one-time analysis window, set `AI_ENABLED_UNTIL` to an
offset-aware absolute timestamp. No request starts at/after that instant; in-flight
requests are cancelled at the boundary, and unknown usage retains its reservation.
The cutoff survives process sleep/restart; it is not a daily automatic re-enable.
`AI_IMPORT_CHAT_ID` opts exactly one authorized group's imported review queue into
analysis (including expired raw history retained for required extraction). Existing
reviewed/completed records and terminal jobs are not repeatedly sent to the model.
The permanent group bootstrap is separate: it persists a structure/topic map first,
then assigns all available sources to a new analysis generation. Earlier terminal
jobs remain auditable but do not block that explicitly requested generation.
Past/future context is bounded around the original source timestamp, not around the
latest end of a large imported history. Calendar-known subject aliases are normalized
before next-lesson fallback. Render sleeping still pauses execution; it does not erase
the SQL queue or extend the analysis cutoff.

The SQL inbox survives missed broker wakeups. Leases and attempt fencing prevent
stale replies from replacing newer results. КТ/assessments are persisted separately
from homework; explicit dates override provisional next-class homework dates.
Low-confidence and multiple-task candidates remain for review.

Incidents are deduplicated in SQL. Actual private Telegram delivery is unverified;
it requires a configured owner ID and the owner having started the bot. Regression
tests use a fake provider and do not spend DeepSeek balance.

Before live-group readiness: verify real Telegram webhook/onboarding, extraction
quality after the latest fixes, processor restart/recovery with live messages,
owner alerts, and encrypted off-machine backup/restoration. A bounded real-provider
evaluation returned 5/8 before subsequent fixes; it is not a completed quality gate.

No production secrets belong in this directory.
