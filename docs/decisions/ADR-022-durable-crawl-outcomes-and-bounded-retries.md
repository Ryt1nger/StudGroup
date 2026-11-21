# ADR-022: Durable crawl outcomes and bounded retries

Status: accepted

Date: 2026-11-21

## Context

An SDO account attempt can commit its queue result and then lose the owner notification when the
process stops before the separate Telegram outbox transaction. Rebuilding a message from the queue
row alone loses the last verified stage and per-attempt counters. Missing counters were therefore
shown as zero, and a timeout during link enrichment looked like a total source failure.

Transient crawl failures also had bounded backoff but no terminal attempt budget. A production
CAPTCHA job reached 28 attempts and an expired lease could be reclaimed indefinitely.

## Decision

- Persist only a closed, privacy-safe outcome on `ranepa_crawl_observations`: failure detail,
  diagnostic code, last stage and aggregate saved/discovered counts.
- New outcomes start in `notification_state=pending` only while per-account owner notifications
  are enabled. Legacy and notification-disabled observations remain `NULL` and are never replayed
  automatically.
- At the next worker start, pending outcomes are reconstructed through the normal typed notification
  path. The existing outbox dedup key makes recovery safe after a crash at any point.
- An absent counter is unknown, never zero. A link timeout after saved weeks is a partial result.
- CAPTCHA, source, parse, identity and internal errors have explicit attempt budgets. Exhaustion
  moves a job to `dead_letter`; only an explicit scoped force refresh can reopen it.
- Invalid credentials remain `failed/credentials_required` and require user action. Repeated expired
  leases become `dead_letter/lease_expired_exhausted`.

## Consequences

- Migration `0066` is additive and leaves all legacy observations untouched.
- Notification recovery is allowed to fail without blocking the actual crawl.
- The database still contains no URLs, credentials, page HTML, cookies or private source text.
- Operations must investigate `dead_letter` instead of blindly replaying it.
- Downgrade removes the new outcome columns and index. Existing `dead_letter` rows must be reviewed
  before rollback to code that does not understand this state.
