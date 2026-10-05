# Backend

Owner: Codex.

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
ingestion, homework endpoints and extraction remain in progress. Cancellation
and reschedule overrides are not yet exposed.

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
