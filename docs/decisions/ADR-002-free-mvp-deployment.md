# ADR-002: Portable free pilot deployment

Status: accepted

Date: 2026-10-03

## Availability checkpoint — 2026-10-06

The owner selected Render Free instead of acquiring a VM. The API, static frontend
and PostgreSQL 17 are provisioned and deployment health checks pass. No Oracle
capacity is assumed. Render's free database expires on 2026-11-05; its API can sleep
after inactivity. The application processor runs inside the API using durable SQL
leases, rather than a separate paid worker. AI is initially disabled. An external
cron was mentioned by the owner but its setup/interval have not been verified.

## Context

The MVP should operate continuously without the product owner's laptop and should initially minimize infrastructure cost.

## Options

- Oracle Cloud Always Free VM with containerized services.
- Google Cloud free VM plus multiple managed free services.
- Free sleeping web hosting plus managed free databases.

## Decision

Use Render Free for the temporary pilot: FastAPI web service, managed PostgreSQL
and static frontend. Keep the containerized PostgreSQL/Redis/Celery topology as an
optional migration path, not a provisioned service. Persistent backups and a
non-expiring database destination remain required before long-term group operation.

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
