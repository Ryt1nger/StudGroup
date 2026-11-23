# Production runtime on the StudGroup VPS

This Compose project runs the StudGroup API, its operational Redis queue, and a Caddy image that
serves the production frontend on the VPS.
PostgreSQL/Supabase and YDB remain managed external services and are never copied into this stack.

## Required host state

- `/etc/studgroup/backend.env` exists, is owned by `root`, and has mode `600`.
- The env file contains production connection credentials. Never commit it.
- Docker Compose v2 is installed and the firewall exposes only SSH, HTTP, and HTTPS.

## Deploy

Deploy only a commit already merged to GitHub `main`. From the repository root:

```sh
git fetch origin
git checkout --detach <merged-sha>
export STUDGROUP_IMAGE_TAG=<first-12-characters-of-merged-sha>
docker compose -f deploy/server/compose.yml build api
docker compose -f deploy/server/compose.yml build caddy
docker compose -f deploy/server/compose.yml up -d redis api caddy
```

The normal path is automated: a successful `Backend` workflow for `main` triggers
`.github/workflows/deploy-vps.yml`. Its restricted SSH key can invoke only
`deploy/server/deploy-wrapper.sh`, which passes the original request to `deploy.sh`; the server
validates the exact current `origin/main` SHA, serializes
deploys, runs health checks, and rolls back to the previous SHA if verification fails. Manual
commands above are the recovery path, not the routine release process.

Validate `docker compose ps`, `/health`, `/ready`, HTTPS, logs, and external dependencies before
changing the Telegram webhook or starting any batch SDO processing. The temporary public endpoints
are `https://api.109-237-97-85.sslip.io` and `https://app.109-237-97-85.sslip.io`. Paid AI stays off
until separately approved.

## Rollback

Check out the previously recorded merged SHA, rebuild the API with its SHA tag, and run the same
`docker compose up -d` command. Redis data and Caddy certificates live in named volumes and are not
removed during a rollback.

## Group billing rollout (Germany)

The authoritative database is **Supabase Frankfurt** project `qzninfmbjegxuxncsueu`.
The older US West project `ytghmfxjjyxkcaglbhfj` is not a target. YDB remains unchanged.
The code introduces Alembic revision `0067`, which adds `group_subscriptions`
and `group_payment_orders` with Row Level Security and no client access.

**Important:** `deploy.sh` does not automatically run Alembic migrations.
Before enabling billing, migrate the *new Germany database* from `0066` to `0067`
using the deployed backend's `alembic upgrade head`, and verify `alembic current`.
Do not run a migration against US West or an unverified `DATABASE_URL`.

The bot starts with `BILLING_ENABLED=false`, `BILLING_STARS_PER_MONTH=0`,
and `BILLING_ENFORCE_ACCESS=false`. Leave these off during first rollout. Only
set the Stars price with operator approval, after a sandbox payment test and
verification of one-time 1–4 month checkout, replay protection, refunds,
legal receipts and backend backups. Enabling checkout does not automatically
enable access enforcement; that is a separate release decision.

Telegram digital access may only be sold **inside Telegram via Stars**. This
branch does not fabricate bank transfer URLs or use individual card transfers.
An external bank/merchant integration requires a separate compliant checkout,
merchant credentials and server-side settlement verification.

Production deployment follows the successful GitHub `Backend` workflow for
`main` and the protected `Deploy VPS production` workflow. If the
VPS SSH identity or host variables are unavailable, do not deploy via a
historic Render service. Investigate GitHub Actions logs and ask the owner to
restore the actual VPS access path.
