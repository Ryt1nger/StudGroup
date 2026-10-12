# AI analysis integration report

**Branch:** `codex/ai-analysis-core`

**Date:** 2026-10-12

**Verdict:** `offline-ready`

## Completed evidence

- Celery and embedded runtime now enter through `analysis.dispatch.dispatch_next`.
- The durable legacy claim excludes any group explicitly configured as `new`. Missing settings,
  `legacy` and `shadow` continue through the legacy writer, so an accidental mode switch cannot
  publish through both engines.
- Offline C+ validation, canonical resolution and compatibility projection are covered by E2E
  component tests, including multiple cards from one source, locks, revisions and notification
  idempotency.
- The full backend suite passed against PostgreSQL 16.15: **423 passed, 0 skipped**.
- A clean disposable database completed `upgrade base -> 0031`, `downgrade 0031 -> base`, and a
  second `upgrade base -> 0031`. The PostgreSQL concurrent worker test passed.

## PostgreSQL environment

The test server came from the official Postgres.app 2.9.6 PostgreSQL 16 image, ran from a
temporary directory and exposed only a temporary Unix socket. No production connection or data
was used. Migration output included the source-card uniqueness relaxation, projection links,
group routing, shadow summaries and mode history.

## Quality evidence available without paid calls

- The committed synthetic corpus validates multi-item extraction, negative examples, evidence,
  split isolation and non-invention rules.
- Saved source 3338 states tasks 10–16 on pages 43–44 by the following Wednesday. Its archived
  legacy response found that homework; the documented historical loss occurred in the old batch
  screening stage.
- Saved source 3845 has an archived legacy extraction for a mathematical-analysis assignment
  without a deadline.
- These saved artifacts are not a labelled held-out comparison of both engines. There is no saved
  current C+ provider response, and no new provider call was authorized. Precision, recall, F1,
  comparative cost and concrete C+ error cards therefore cannot be reported honestly yet.

## Remaining blockers

1. The worker does not yet execute the C+ shadow pass for `shadow` groups.
2. The worker deliberately leaves `new` groups queued; the canonical writer is not reachable
   through production dispatch and `new` remains forbidden.
3. The real private discussion export has not been transformed into an adjudicated held-out gold
   corpus. Messages must not be labelled from memory or committed with private text.
4. A paid shadow sample and owner-approved budget are required for comparative quality/cost.

## Safe paid-shadow procedure

After owner approval, use one non-production group, retain `legacy` as sole writer, sample 5–10%
of eligible completed deep jobs, cap shadow at USD 0.02 per day initially, and stop automatically
at the cap. Persist only structured outcomes, hashes, token counts and 30-day restricted traces.
Review the first 20–30 academic entities manually before increasing sampling. Any shadow product
write, cross-group evidence, unexplained loss or invented critical fact is an immediate stop.

`shadow-ready` requires the missing worker shadow execution plus a zero-side-effect E2E test.
`pilot-ready` additionally requires adjudicated held-out metrics, approved thresholds and a
successful real shadow observation period.
