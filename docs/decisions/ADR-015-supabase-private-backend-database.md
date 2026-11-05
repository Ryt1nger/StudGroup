# ADR-015: Private Supabase database for the existing backend

Status: proposed for production cutover; test restore accepted

Date: 2026-11-05

## Context

The Render Free PostgreSQL database expires on 2026-11-05 at 19:37 Moscow time.
StudGroup uses Mini App → FastAPI → PostgreSQL. The existing Supabase Free project
has PostgreSQL 17 and enough room for the verified 71-table snapshot.

## Options

- Expose tables through Supabase Data API and add policies for each client role.
- Keep the existing backend as the only application database client, and exclude
  `public` from the Data API.

## Decision

Keep the existing backend architecture. `public` is excluded from Exposed schemas,
and automatic publication of new tables is off. The restored tables are owned by a
dedicated `studgroup_app` login role with no superuser, database creation, role
creation, replication, or RLS bypass capability. It can create objects only in
`public`, for controlled Alembic migrations. `anon` and `authenticated` receive no
table DML or sequence access. RLS is enabled on all application tables as a second
barrier; no public policies are needed. System schema privileges remain unchanged.

The backend uses the observed Session Pooler on port 5432. For a future cutover,
enable certificate and hostname verification with the project CA and set a small
SQLAlchemy pool through opt-in environment settings. The current Render PostgreSQL
connection keeps its existing behavior until those settings are applied.

## Consequences

- No Mini App database key is needed; all authorization remains in FastAPI.
- The application role owns its tables and bypasses their RLS as owner. Its password
  must remain in protected service configuration and a separate private backup.
- Supabase advisor reports `rls_enabled_no_policy` as informational for the private
  tables. Public grants and Data API exposure must be rechecked after every restore.
- The Supabase `ensure_rls` event trigger did not enable RLS during `pg_restore`, so
  the restore procedure explicitly enables it and verifies all 71 tables.
- Six source sequences lagged explicitly written IDs. Every final restore must
  validate and advance sequences before accepting new writes.
- Production deployment, webhook change, and `DATABASE_URL` switch require separate
  owner approval in stage 5.
