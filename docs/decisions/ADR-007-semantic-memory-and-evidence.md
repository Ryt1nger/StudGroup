# ADR-007: Semantic memory, source evidence and item identity

Status: accepted

Date: 2026-10-12

## Context

Telegram discussions distribute one academic fact across announcements, replies,
clarifications and corrections. Re-sending the full chat on every call is expensive, while
analysing each message independently loses context and creates duplicates.

## Decision

- Persist the permitted source messages, revisions and attachment metadata.
- Maintain four structured memory layers: group knowledge, active conversation segments,
  canonical academic items and append-only fact/item history.
- Every material fact carries source evidence, confidence state and validity where relevant.
- Model-proposed memory remains a hypothesis until deterministic or human confirmation.
- Each AI call receives new/changed messages plus only the relevant memory slice and related
  items. The full chat is not replayed.
- One discussion may yield zero, one or many task/deadline cards; one item may cite many
  messages.
- Schedule-derived homework dates remain explicitly distinguishable from stated dates.

## Consequences

Late clarifications can update an existing item without replaying the full archive. The
system must implement group isolation, evidence-aware retrieval, versioning and retention;
free-text summaries cannot be an authoritative source for critical facts.
