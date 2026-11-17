# StudGroup — product reliability audit (release candidate)

Date: 2026-11-17. Scope: draft PR #194, `release/reliability-audit-2026-11-16`.

## Release verdict

**BLOCKED until all regression tests, migration checks and independent review pass.**
Do not equate green legacy CI with proven P0/P1 correctness. No merge, production
deploy, production schema update, paid AI batch or real student-account crawl is
authorized by this document.

## P0 blocking scenarios and fixes under review

1. **Replacement of materialized schedule**. Former `ranepa_materialization.py`
   only upserted and never retired rows absent from a newer complete source read.
   New full-source version and provenance target the source/week only; partial
   or identity-unverified reads cannot delete peer data. Personal facts and
   independent meeting URLs remain authoritative. A fully verified empty week
   can retire old common rows but cannot prove fresh crawl coverage.
2. **False `covered` status**. Former `_coverage_satisfied()` inferred delivery
   from any recent verified peer snapshot, even when the worker suppressed a
   failed fan-out. New receipts identify group, recipient, source, week, source
   digest, check time and exact common/subgroup coverage. They are created with
   recipient rows in the same transaction. Missing or stale evidence keeps
   backups eligible; materialization failure returns a crawl failure.
3. **Unresolved photos blocking actionable captions**. Former
   `_self_contained_academic_reply()` required a reply ID. Independently
   actionable text on the problematic image itself now follows the same
   conservative path as an independent reply. Ambiguous/deictic text remains
   image-dependent, and unrelated text stays eligible. No unverified OCR
   should enter a text-only package.

## Still partially solved or not yet proven

- Direct Moodle/SDO meeting-link extraction: link-state preservation and
  frontend action safety do not demonstrate better extraction recall.
- AI recognition P0-6: product outcome telemetry is not an accuracy benchmark.
  A de-identified ground-truth set and approved, cost-bounded before/after
  evaluation remain necessary.
- CAPTCHA: a provider's solve response is not a completed recovery; verify
  challenge disappearance, protected page access, identity, group, week and
  saved data. Historical complete recoveries: 9/25, about 36%; no new
  production metric proves stable RuCaptcha operation.
- Six-hour crawl schedule: not a hard SLA. GitHub-hosted watchdog shares
  GitHub Actions failure modes; Render sleeping and background AI/outbox
  workers are not independently guaranteed.
- Published timetable merging: partial personal reads must not remove
  published classes. Explicit verified cancellations must be honored.
  Ambiguous matches and moved times need regression coverage and canary.
- Existing endpoints and generated TypeScript should be unchanged by internal
  receipt metadata; contracts remain at their current documented version.

## Database compatibility and controlled rollout

Migration 0065 follows 0064, adding `source_complete`, `source_digest`
and `ranepa_materialization_receipts`. No legacy source or delivery receipt
is backfilled as verified. Review migration 0053 → head and downgrade on a
nonproduction database. Old materialized rows without provenance are not
trusted by the revised view until new full verification. Downgrading 0065
discards receipt evidence and must re-enable real verification rather than
reinstating false `covered` conclusions. Roll out schema/backend before
frontend only with separate owner permission, real canary and rollback plan.

## Required evidence before review approval

- All new replacement/empty/partial/personal/subgroup/rollback and
  receipt freshness/failure/multiple-group tests pass repeatedly.
- All independent-caption/reply/deictic/OCR and later-image-update tests
  pass, with product outcomes tied to actual target mutations.
- Ruff, formatter, full backend tests, database migrations, TypeScript,
  frontend unit tests/build, Playwright and OpenAPI drift pass on final SHA.
- Source quality, CAPTCHA stability, meeting-link improvement and real
  production SLA remain explicitly unclaimed until owner-approved canaries.

This is the canonical audit, not a claim that all P0/P1 have been resolved.
