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
