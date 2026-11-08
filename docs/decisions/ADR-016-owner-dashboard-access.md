# ADR-016: Operator-only dashboard access
Status: accepted
Date: 2026-11-08

## Context

The operator needs current app and AI metrics from Telegram without exposing group
messages or credentials. The deployed backend already verifies Telegram WebApp
`initData` and issues short-lived database-backed sessions.

## Options

- Reuse the Telegram session and check the configured bot operator ID on every dashboard request.
- Add a separate password or a public metrics endpoint.

## Decision

Serve a read-only dashboard at `/admin/` from the backend origin. The bot offers a
WebApp button only in the private chat of `BOT_ADMIN_USER_ID`. The dashboard reuses
the existing Telegram bootstrap; each dashboard API request verifies its bearer
session and the current configured operator ID. Data is aggregated from current
database rows, with no raw chat text, provider response or credential fields.

## Consequences

The page requires an HTTPS `ADMIN_PAGE_URL` in production and a tap on Telegram's
WebApp button. Revoking or changing `BOT_ADMIN_USER_ID` takes effect on the next
request. The endpoint is additive and does not change student API responses.
