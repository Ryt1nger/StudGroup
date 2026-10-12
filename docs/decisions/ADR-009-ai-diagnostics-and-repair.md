# ADR-009: AI diagnostics, bounded repair and retention

Status: accepted

Date: 2026-10-12

## Context

A non-empty provider answer can currently end as an unexplained successful empty result.
Development also needs to distinguish provider mistakes from parser/application mistakes
without permanently retaining private chat-derived payloads.

## Decision

- Persist explicit per-attempt and per-proposal outcomes. Valid empty and all-rejected are
  different states; valid siblings always survive an invalid sibling.
- Permit at most one repair request only for a technically unparseable or truncated provider
  response. Missing optional facts, ambiguity or unusual academic wording do not trigger it.
- Attribute repair tokens/cost independently and retain both attempt outcomes.
- During development and shadow, a feature flag may retain the full provider response for
  technical administrators. It expires after 30 days and is disabled after diagnostics
  stabilize unless separately re-approved.
- Ordinary logs and structural outcomes exclude raw chat/provider content.
- A read-only production diagnostic may inspect the two disputed historical messages'
  statuses, attempts, rejection reasons and projection links. It cannot mutate, replay, lock
  or block processing.

## Consequences

Silent loss becomes observable and replayable. The diagnostic trace adds storage and privacy
responsibility but does not consume additional provider tokens. Production rollout of trace
retention remains disabled by default.
