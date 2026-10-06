# ADR-002: Portable free pilot deployment

Status: accepted

Date: 2026-10-03

## Availability checkpoint — 2026-10-06

The owner has no server. No Oracle account or free VM capacity has been verified,
and no resources have been provisioned. The preference below is not an available
deployment. Container definitions remain portable; selecting/acquiring a host is
pending owner direction. Do not assume eligibility, free capacity or permission
to purchase infrastructure.

## Context

The MVP should operate continuously without the product owner's laptop and should initially minimize infrastructure cost.

## Options

- Oracle Cloud Always Free VM with containerized services.
- Google Cloud free VM plus multiple managed free services.
- Free sleeping web hosting plus managed free databases.

## Decision

Use an Oracle Cloud Always Free VM for the pilot deployment of FastAPI, PostgreSQL, Redis, Celery, and the scheduler. This is pilot infrastructure, not the assumed long-term production topology. Use Cloudflare Pages for the frontend and Cloudflare R2 for object storage and encrypted backups.

Use Docker Compose locally. Production may initially use the same container definitions with production overrides, while preserving the option to move to an orchestrated platform later.

Begin with one conservatively configured Celery worker and the smallest queue topology required by the active vertical slice. Add specialized workers only when workload isolation is measured or required for correctness.

## Consequences

- Initial infrastructure cost can remain close to zero.
- The team is responsible for server updates, hardening, monitoring, and recovery.
- A single VM is an accepted pilot availability and resource-contention risk.
- Vendor-neutral interfaces and tested backups are mandatory.
- Staging becomes mandatory before real payment integration.
- API latency, queue age, database health, memory pressure, and AI workload are monitored closely enough to identify when the single VM is no longer safe.
- The deployment must move or split services before paid growth if pilot measurements show repeated resource contention, missed processing windows, or unsafe recovery characteristics.
