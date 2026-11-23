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

The authoritative DB is Supabase Frankfurt `qzninfmbjegxuxncsueu`, never
the old US West `ytghmfxjjyxkcaglbhfj`. YDB remains unchanged.
The previous Stars billing schema is revision `0067`; manual bank-link
orders require **revision `0068`**. `deploy.sh` does **not** run Alembic.
Before enabling the bank-link flow, run `alembic upgrade head` against the
verified German production DB and confirm `alembic current` is `0068`.
Never run this against the old database.

### Alfa-Bank manual checkout

The owner supplies **four actual HTTPS payment links** in
`/etc/studgroup/backend.env`. Generate them via the bank's supported
self-employed payment-link feature, with these **fixed amounts**:

- `BILLING_ALFA_URL_1M`: 800 RUB
- `BILLING_ALFA_URL_2M`: 1600 RUB
- `BILLING_ALFA_URL_3M`: 2400 RUB
- `BILLING_ALFA_URL_4M`: 3200 RUB

The app never manufactures Alfa URLs or modifies bank parameters. No
bank API credentials are required. The four links must already point to
the owner's actual account and carry the intended payment amounts.

Set `OWNER_TELEGRAM_USER_ID` (or `BOT_ADMIN_USER_ID`) to the Telegram
numeric ID that is allowed to approve payments. Enable only after link,
recipient, and amount checks:

```sh
BILLING_PAYMENT_MODE=manual_alfa
BILLING_ENABLED=true
BILLING_ALFA_URL_1M=https://<actual-bank-issued-800-rub-link>
BILLING_ALFA_URL_2M=https://<actual-bank-issued-1600-rub-link>
BILLING_ALFA_URL_3M=https://<actual-bank-issued-2400-rub-link>
BILLING_ALFA_URL_4M=https://<actual-bank-issued-3200-rub-link>
BILLING_ENFORCE_ACCESS=false
```

The student opens the link and returns to click "Я оплатил". This creates
a **review request**, not a credit. The owner receives a private bot
message with group, period, amount, Telegram payer ID and order reference.
Only that authenticated owner can click "Подтвердить получение" and
extend the group's paid period, exactly once, after checking their bank.
"Не поступило" rejects without extending access. On confirmation, the
student's existing single-message start panel shows success, then changes
back to the normal home panel about 20 seconds later. Telegram does not
provide bot read receipts; this delay is **after confirmation**, not
guaranteed to be 20 seconds after the person reads it. The student can
return home immediately using the on-screen button.

Manual verification is operationally mandatory: a bank link and a
student's click cannot prove a payment. Do not approve two different
order references for the same bank receipt. Always check transaction
amount and beneficiary in the bank.

The legacy Stars code and `BILLING_STARS_PER_MONTH` remain only for
compatibility with old invoices and have no role in `manual_alfa` mode.
If Telegram has granted the owner an exception for external checkout of
digital access, retain the written permission and its scope; the project
does not independently verify that authorization.

Production deployment follows successful Backend CI and the protected
VPS workflow. If this fails, inspect GitHub Actions and server logs.
Do not use the historic Render service. Never enable access enforcement
during first rollout.
