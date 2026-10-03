# Module 1: Technical foundation and infrastructure

Status: approved  
Date: 2026-10-03  
Final product authority: Ryt1nger

## 1. Outcome

Module 1 establishes a portable technical foundation for the StudGroup MVP. The system must run continuously without a developer laptop, support independent backend and frontend development, and remain easy to migrate from free infrastructure to managed paid services.

## 2. Approved stack

| Area | Decision |
| --- | --- |
| Backend runtime | Python |
| HTTP framework | FastAPI |
| API style | REST with OpenAPI as the source of truth |
| Frontend integration | Generated TypeScript client and types |
| Backend architecture | Modular monolith |
| Database | PostgreSQL |
| ORM | SQLAlchemy 2.0 async |
| Migrations | Alembic |
| Background jobs | Celery |
| Queue and short-lived cache | Redis |
| File storage | S3-compatible object storage plus Telegram `file_id` |
| Local development | Docker Compose |
| Production MVP | Oracle Cloud Always Free VM |
| Frontend hosting | Cloudflare Pages |
| Production object storage | Cloudflare R2 |

The frontend framework is selected by the frontend owner and approved by the product owner. It must consume the shared OpenAPI contract and generated types.

## 3. Architecture

The backend is one deployable codebase divided into isolated modules:

- identity and membership;
- groups and roles;
- Telegram ingestion;
- raw messages and attachments;
- academic events and cards;
- group and personal schedules;
- AI pipeline;
- questions and digests;
- notifications;
- subscriptions and payments;
- referrals;
- audit and retention.

FastAPI and Celery run as separate processes but share domain rules and persistence code. A module may be extracted into a service later only when scale or isolation requirements justify it.

## 4. Backend and frontend contract

1. FastAPI produces the OpenAPI document.
2. The approved OpenAPI document is stored in `packages/contracts`.
3. TypeScript types and a client are generated into `packages/shared-types`.
4. Frontend code does not maintain a conflicting handwritten API model.
5. CI detects contract drift and incompatible changes.
6. Loading, empty, partial, validation, permission, and failure states are included in endpoint contracts.
7. WebSocket or Server-Sent Events are added only when polling or normal REST responses are insufficient.

## 5. Data rules

- PostgreSQL is the source of truth.
- Important domain fields use typed columns and constraints.
- JSONB is reserved for genuinely flexible AI or provider payloads.
- Every schema change uses an Alembic migration.
- Migrations must be testable both on an empty database and on the previous production schema.
- Redis never stores the only copy of business data.
- S3 storage is accessed through a provider-neutral interface.

## 6. Background work

Celery queues are separated by workload:

- fast ingestion and classification;
- AI extraction and re-analysis;
- files and media;
- notifications;
- scheduled reviews and retention;
- payments and provider reconciliation.

Required behavior:

- stable idempotency keys;
- bounded retries with backoff;
- task timeouts;
- dead-letter or failed-task inspection;
- priority for urgent and super-urgent events;
- metrics for queue depth, age, retries, failures, tokens, and AI cost.

Redis is also allowed for short-lived caching, rate limits, and distributed locks.

## 7. Files and media

- Always preserve Telegram `file_id` and source metadata.
- Copy only media required for analysis or reliable delivery.
- Production copies use S3-compatible object storage.
- Local copies follow the approved 30-day retention rule.
- Local development uses an S3-compatible container.
- Application code must not depend on one storage vendor.

## 8. Environments

### Local

Docker Compose starts PostgreSQL, Redis, local object storage, FastAPI, Celery workers, and the scheduler. Frontend development may run outside Docker for faster hot reload.

### Production MVP

The initial production environment uses an Oracle Cloud Always Free VM. Backend services run in containers. Cloudflare Pages hosts the frontend and Cloudflare R2 stores files and encrypted database backups.

### Staging

A permanent staging environment is not required for the first MVP. It becomes mandatory before real payment integration. Pull requests receive frontend previews and automated backend checks before staging exists.

## 9. Portability

The free VM is a deployment target, not a platform dependency.

- Database configuration uses a standard PostgreSQL URL.
- Redis configuration uses a standard connection URL.
- Object storage uses an S3-compatible API.
- Services are built as portable container images.
- Configuration is supplied through environment variables.
- No Oracle-specific domain or persistence feature is allowed.

The expected growth path is managed PostgreSQL, managed Redis, multiple API instances, and multiple Celery workers without changing product contracts.

## 10. Testing

The MVP uses a balanced suite:

- unit tests for domain rules;
- integration tests with PostgreSQL and Redis;
- OpenAPI contract tests;
- Alembic migration tests;
- critical end-to-end scenarios;
- production smoke tests after deployment.

Tests must cover idempotency, permissions, state transitions, retries, time zones, retention, and contract compatibility.

## 11. CI/CD

Every pull request must:

- run formatting and static checks;
- run unit and integration tests;
- validate OpenAPI and generated types;
- validate Alembic migrations;
- build backend and frontend artifacts;
- expose a frontend preview when supported.

Merging to `main` creates a release candidate. Production deployment requires explicit product-owner confirmation. Deployment performs a pre-migration backup, applies migrations, starts the new containers, and runs smoke tests.

## 12. Monitoring

The MVP includes:

- structured JSON logs;
- centralized backend and frontend error reporting;
- `/health` and `/ready` endpoints;
- external uptime checks;
- Celery queue, age, retry, and failure monitoring;
- alerts when API, workers, or scheduled jobs fail;
- per-group and per-reasoning-level DeepSeek usage and cost metrics.

A full Prometheus, Grafana, and Loki stack is deferred until real usage justifies the memory and maintenance cost.

## 13. Secrets

- No production secret is committed to Git.
- `.env.example` contains names and safe placeholders only.
- GitHub Secrets supplies deployment credentials to CI/CD.
- Production secrets are stored on the server with restricted filesystem permissions.
- Logs redact tokens, passwords, signatures, payment identifiers where necessary, and connection strings.
- Each credential has a documented rotation and revocation procedure.
- Only the product owner holds production secret access during the MVP phase.

## 14. Backups and recovery

- Create an encrypted PostgreSQL backup every day.
- Keep 7 daily, 4 weekly, and 3 monthly backups.
- Store backups separately in Cloudflare R2.
- Create an additional backup before every production migration.
- Redis is not backed up because it contains no authoritative business data.
- Temporary media copies are not included in database backups.
- Run an automated restore verification at least monthly.
- A backup is not considered valid until restore verification succeeds.

## 15. Module acceptance checklist

- [x] Backend language and framework selected.
- [x] Backend/frontend contract strategy selected.
- [x] Backend architecture selected.
- [x] Database, ORM, and migration system selected.
- [x] Queue and cache selected.
- [x] Object-storage strategy selected.
- [x] Local environment selected.
- [x] Initial free production topology selected.
- [x] Portability to paid infrastructure required.
- [x] Development and production environments defined.
- [x] Testing strategy defined.
- [x] CI/CD release gate defined.
- [x] Monitoring baseline defined.
- [x] Secret-storage strategy defined.
- [x] Backup and recovery policy defined.
- [ ] Frontend framework proposal reviewed and approved separately with Claude.
- [ ] Oracle Cloud and Cloudflare accounts provisioned during implementation.

The remaining unchecked items are implementation inputs and do not change the approved backend foundation.

