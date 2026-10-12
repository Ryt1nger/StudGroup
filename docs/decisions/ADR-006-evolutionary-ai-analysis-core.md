# ADR-006: Evolutionary C+ AI analysis core

Status: accepted

Date: 2026-10-12

## Context

The current Telegram, PostgreSQL and Celery pipeline already protects source revisions,
human locks, budget reservations and public API compatibility. Its semantic boundary is
too narrow: analysis is centred on one target message, related facts can be silently lost,
and technical completion is indistinguishable from useful application.

## Options

1. Change only the prompts or provider.
2. Replace the whole backend analysis pipeline.
3. Preserve the operational foundation and incrementally replace the semantic core.

## Decision

Use option 3, the C+ architecture described in
`docs/architecture/ai-analysis-architecture.md`.

- Preserve Telegram ingress, PostgreSQL/Celery, revision/generation guards, locks, budgets
  and existing WebApp contracts.
- Introduce versioned inputs/outputs, discussion segments, structured memory, canonical
  academic items and explicit application outcomes additively.
- Keep technical attempt, academic interpretation and product application statuses
  separate.
- Run the new engine in shadow before allowing product writes. Exactly one engine is the
  writer for a group.
- A rollback selects the legacy writer and retains new shadow data read-only.
- The worker captures every new or edited source after a group enters shadow mode, including
  sources rejected by legacy relevance heuristics. Sampling decisions are persisted so a
  skipped source is not reconsidered on every sweep.
- Shadow work uses its own durable lease/retry state and a per-group daily budget. A fixed
  worst-case reservation is taken under the locked group settings row before either C+ call;
  reported usage is settled afterward, while ambiguous provider failures retain the safe
  reservation as a conservative charge.
- The shadow executor has no dependency on canonical projection, card publication or
  notification code. It stores the immutable input, bounded structured memory, validated
  results, sanitized errors, token usage and provider-payload hashes for 30 days.

## Consequences

Delivery requires staged migrations and compatibility adapters, but it avoids reimplementing
the concurrency and safety guarantees that already work. Quality and cost are measured on
identical source snapshots before a pilot writer switch.

The isolation boundary intentionally means a successful C+ result cannot alter product data.
Only the legacy path writes while a group is in `shadow`; enabling `new` remains a separate,
explicitly gated decision.
