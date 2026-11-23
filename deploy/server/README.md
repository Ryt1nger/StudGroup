# Production runtime on the StudGroup VPS

This Compose project runs the StudGroup API, its operational Redis queue, and Caddy on the VPS.
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
docker compose -f deploy/server/compose.yml up -d redis api caddy
```

Validate `docker compose ps`, `/health`, `/ready`, HTTPS, logs, and external dependencies before
changing the Telegram webhook or starting any batch SDO processing. Paid AI processing stays off
until separately approved.

## Rollback

Check out the previously recorded merged SHA, rebuild the API with its SHA tag, and run the same
`docker compose up -d` command. Redis data and Caddy certificates live in named volumes and are not
removed during a rollback.
