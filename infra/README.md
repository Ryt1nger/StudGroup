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
database expires on 2026-11-05; it is not permanent storage. No private chat export
is uploaded as part of deployment. Actual live Telegram access is a separate step.

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
No private chat data has been migrated to Render and AI remains disabled.

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

AI calls are disabled by default. Once explicitly enabled, SQL reserves budget
before each request, records usage and caps each source revision at two attempts.
Timeouts with unknown usage retain their full reservation. Initial caps: $0.05 per
UTC day per group and $1.70 total in this processing ledger. Other account consumers
and manual import scripts are outside this ledger; these limits do not guarantee
provider balance or extraction quality.

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
