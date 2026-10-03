# ADR-001: Python modular monolith

Status: accepted  
Date: 2026-10-03

## Context

StudGroup combines Telegram ingestion, AI analysis, files, schedules, notifications, payments, and a WebApp. The MVP needs fast delivery without losing module boundaries.

## Options

- Python and FastAPI modular monolith.
- TypeScript and NestJS.
- Go services.
- Microservices from the beginning.

## Decision

Use Python, FastAPI, and a modular monolith. Run HTTP and background workloads as separate processes from one codebase. Use PostgreSQL, SQLAlchemy 2.0, Alembic, Celery, and Redis.

Expose REST through OpenAPI and generate the frontend TypeScript client.

## Consequences

- AI and media integration remain straightforward.
- The frontend stays type-safe despite using a different language.
- Operational complexity remains suitable for an MVP.
- Module boundaries must be enforced in code review because deployment boundaries do not enforce them.
- Services may be extracted later without changing domain meaning.

