# ADR-012: Owner-only stored shadow export

Status: accepted

Date: 2026-10-16

## Context

The owner needs the private stored C+ wave for offline comparison when Render Dashboard, Shell
and external PostgreSQL access are unavailable. The running backend already has private-network
database access and an authenticated Telegram admin channel. The export contains chat text and
must not become a public API, server file, log entry or durable outbox payload.

## Decision

- `/shadowexport` is available only in the configured `BOT_ADMIN_USER_ID` private chat.
- It exports only the fixed `wave-a-spread-1` after a 15-minute, single-use nonce confirmation.
- The wave's stored `group_id`, rather than current pilot status or group count, binds the request.
  The binding is checked when presenting, confirming and delivering the export.
- The outbox stores identifiers only. The delivery worker claims the request at most once,
  generates the existing `export_wave` bundle, rolls back the read transaction, streams JSON into
  a bounded in-memory gzip buffer and sends it with Telegram `sendDocument`.
- An ambiguous Telegram result is never retried automatically. A new owner confirmation is
  required. No DeepSeek provider is involved.
- Telegram retains the resulting document in the owner's cloud chat; the owner must delete it
  after downloading when that retention is no longer wanted.

## Consequences

No public route, schema migration, external database allowlist or server-side export file is
needed. The owner depends on Telegram as the delivery channel. A lost response may require a new
manual export and may mean Telegram received a document whose acknowledgement was lost.
