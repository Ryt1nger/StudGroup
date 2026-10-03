# ADR-002: Portable free MVP deployment

Status: accepted  
Date: 2026-10-03

## Context

The MVP should operate continuously without the product owner's laptop and should initially minimize infrastructure cost.

## Options

- Oracle Cloud Always Free VM with containerized services.
- Google Cloud free VM plus multiple managed free services.
- Free sleeping web hosting plus managed free databases.

## Decision

Use an Oracle Cloud Always Free VM for FastAPI, PostgreSQL, Redis, Celery, and the scheduler. Use Cloudflare Pages for the frontend and Cloudflare R2 for object storage and encrypted backups.

Use Docker Compose locally. Production may initially use the same container definitions with production overrides, while preserving the option to move to an orchestrated platform later.

## Consequences

- Initial infrastructure cost can remain close to zero.
- The team is responsible for server updates, hardening, monitoring, and recovery.
- A single VM is an accepted MVP availability risk.
- Vendor-neutral interfaces and tested backups are mandatory.
- Staging becomes mandatory before real payment integration.

