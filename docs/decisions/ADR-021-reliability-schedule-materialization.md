# ADR-021: Evidence-preserving university schedule sync and coverage-aware materialization
Status: accepted
Date: 2026-11-16

## Context

A personal RANEPA/SDO snapshot can contain fewer occurrences than the published group timetable, lack other subgroups, or fail link enrichment after its timetable was captured. Neither partial reads nor ambiguous link states may silently delete established published lessons or leak a peer subgroup's lessons.

## Options

- Replace entire days with a personal snapshot: rejected due to data loss.
- Crawl every account each time: rejected due to cost and consent/availability constraints.
- Merge identified occurrences and materialize only verified common and compatible subgroup facts: accepted.

## Decision

Preserve each published occurrence unless the same verified source explicitly cancels it. Save timetable evidence in a transaction independent of Moodle link enrichment. Direct meeting links and verified Moodle course fallbacks have different UI actions. A conservative, observed coverage check selects representatives; common facts are replicated to all eligible recipients and subgroup facts only to matching verified memberships. Provenance includes source account, week, scope, subgroup and source snapshot digest. Materialize only a full identity/group/week-verified source, atomically replacing its obsolete rows and recording an applied-recipient receipt before claim_next may declare covered. An error in mandatory fan-out fails the crawl; optional link enrichment is independent. Preserve source check time, link check time and retry reasons. Never let the newest overlapping week conceal an older required week.

## Release constraints

Migration 0065 (after 0064) adds source completeness/version metadata and transactional per-recipient delivery receipts. Legacy rows cannot prove coverage and are not blindly backfilled. The public timetable API remains additive/compatible. No production migration, merge or deploy is implied by accepting this design.

## Consequences

All upserts must be idempotent. Tests must cover one missing published lesson, explicit cancellation, every valid link state, failed CAPTCHA, one representative and at least two subgroups, including no cross-subgroup leakage. No live deployment or paid catch-up follows from this ADR; those are separate owner-authorized gates.
