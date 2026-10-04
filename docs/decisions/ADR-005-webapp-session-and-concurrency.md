# ADR-005: WebApp session and optimistic concurrency

Status: accepted

Date: 2026-10-04

## Context

Slice 1 needs a browser-to-backend session derived from Telegram Mini App
authentication. The frontend must also support a personal `Выполнено мной`
mutation without overwriting a newer AI or human revision of the homework card.

Secrets must not be exposed through URLs, client-readable claims, persistent
browser storage or generated frontend configuration.

## Options

1. Send Telegram `initData` with every request.
2. Exchange Telegram `initData` for a signed JWT stored by the browser.
3. Exchange Telegram `initData` for an opaque, short-lived server-side session.

For writes, either accept the last request unconditionally or require the client
revision it was based on.

## Decision

- `POST /v1/session/bootstrap` accepts raw Telegram `initData` not older than ten
  minutes and validates it on the backend.
- The backend returns a cryptographically random opaque bearer token with at
  least 256 bits of entropy and a 12-hour absolute lifetime.
- Only the SHA-256 token hash is stored server-side. There is no refresh token in
  Slice 1; reopening the Mini App obtains a new session.
- The frontend holds the bearer token in memory only and sends it in the
  `Authorization` header.
- Mutable resources expose a monotonically increasing `revision`. A write sends
  `expected_revision`; mismatch returns HTTP 409 and `revision_conflict` with the
  current revision and `action: refetch`.
- Telegram `start_param` contains only an untrusted homework identifier. Access
  is checked again by the backend.

## Consequences

- Revocation and expiry are immediate and do not depend on JWT claim lifetime.
- A database or Redis lookup is required for each authenticated HTTP request.
- Reloading the WebView requires Telegram bootstrap again, which is acceptable
  for Slice 1 and avoids persistent browser credentials.
- Frontend and backend can safely resolve stale completion toggles without silent
  lost updates.
- A future refresh-token design is a breaking authentication change and requires
  a new decision plus contract compatibility review.
