# ADR-003: Deliver the MVP through pilot vertical slices

Status: accepted

Date: 2026-10-03

## Context

Module 1 defines a sound target architecture, but implementing every production-grade capability before exercising the core product would delay evidence from real student groups. The main early risk is product trust and willingness to pay, not the absence of advanced infrastructure.

## Options

- Implement the complete Module 1 operational target before the first product journey.
- Remove Redis and Celery and build a temporary synchronous prototype.
- Preserve the approved stack while delivering small end-to-end slices and phasing operational requirements.

## Decision

Preserve FastAPI, PostgreSQL, Redis, Celery, OpenAPI, and the portable deployment model. Implement the MVP through complete vertical user journeys, beginning with a text Telegram message becoming a sourced homework card in the WebApp.

Treat Module 1 as the target architecture and classify its implementation obligations as P0, P1, or P2. Start with one conservatively configured Celery worker and the minimum queue topology. Expand worker pools, automation, monitoring, and recovery practices only at their approved gate.

Pilot success is evaluated by `docs/product/pilot-success-criteria.md`, not by the number of infrastructure components completed.

## Consequences

- Real product behavior is testable earlier.
- The team keeps the production-compatible stack and avoids a throwaway synchronous architecture.
- Some operational capabilities remain intentionally incomplete during internal testing.
- Each slice must include the correctness mechanisms it actually exercises, especially idempotency, revision checks, and evidence.
- Payment integration remains blocked until staging and its P2 prerequisites exist.
- The product owner controls any change to slice priority or pilot acceptance targets.
