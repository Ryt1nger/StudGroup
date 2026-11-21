# Reliability release gate — 2026-11-16

## Gate 0 — blocking correctness proof (before independent review)

- [ ] Full verified source replaces its own obsolete common/subgroup rows, including moves, confirmed empty weeks, identity and complete-source validation; incomplete source must not delete peers.
- [ ] Recipient-specific receipt is written with the schedule in one transaction. A simulated mid-write failure leaves no partial rows/receipts and no covered status.
- [ ] Coverage validates current source version, group, student, active membership and every required subgroup; empty/partial/stale (>6h) evidence never skips a backup.
- [ ] Unread image permits fully self-contained captions and replies, while dependent texts remain held and unverified OCR never enters independent analysis.
- [ ] Later successful image results enrich existing cards without duplicates; product outcomes distinguish published/updated/blocked_by_image.
- [ ] Re-run targeted regressions in varied order and time; inspect actual logic, not just green baseline CI.
- [ ] Update reliability audit and independently review known gaps; CI passing is not sufficient for approval.

## Gate 1 — review and CI (no deployment)

- Require green backend and frontend CI on final combined head SHA, including database migration upgrade from revision 0053 through 0066, Ruff/format, backend unit suite, TypeScript, frontend unit suite, production build, Playwright Chromium, and shared-types/OpenAPI generation drift.
- Verify that no merge message includes a [run-sdo-once], [run-sdo-all-once] or other paid-crawl trigger tag.
- Recheck actual Render auto-deploy settings before any later merge. The new workspace's frontend and backend were created with auto-deploy off; do not assume older settings still apply.
- Record the deployed frontend and backend SHAs and the database schema revision. Never assume main equals deployed.

## Gate 2 — controlled release (separate owner authorization)

- Separately approve migration 0064 through 0066: back up DB, test up/down on a nonproduction clone, monitor old/unproven snapshots, and do not backfill delivery receipts or legacy notification outcomes artificially.
- Deploy compatible schema and backend before the frontend, perform readiness/smoke checks and validate the new schedule API.
- Deploy the frontend only after confirming compatibility. Roll back or block feature enablement if the frontend expects an unknown endpoint/field.
- Canary one consenting group, then a group with two subgroups. Confirm no lost published occurrence, no cross-subgroup lesson, source-vs-recipient receipts and a failed-write backup crawl. No real student account without owner authorization.
- Confirm manual refresh only queues a request, course URL never grants Join, CAPTCHA success requires a verified schedule, and no credential or raw text appears in diagnostics.
- Measure actual six-hour coverage from oldest required week and SDO links separately; compare SDO crawler cost and product outcomes before/after. New code does NOT prove better direct-link extraction, AI accuracy, CAPTCHA recovery or a production six-hour SLA.
- Roll back if any preexisting published lesson disappears, a subgroup is exposed, deployment SHAs are incompatible or paid budget would be exceeded.

## Known unresolved external dependencies

Backend Render Free can sleep and GitHub scheduled jobs can be delayed or skipped. The separate hourly GitHub watchdog flags missing scheduled crawl launches through a deduplicated repository issue, but it shares the same GitHub scheduling platform and cannot guarantee independent always-on delivery. YDB/Postgres opaque credential reference reconciliation is not yet verified in production. Do not claim SLA success without those external checks and an owner-approved canary.

## 2026-11-18 migration incident and safe scheduled operations

- Main after #194: `83714e09adef7f620daeec55b49cc05760bf6730`. New API origin: `https://studgroup-api-v2.onrender.com`. Confirm deployed frontend/backend SHAs separately.
- Read-only Supabase check on project `ytghmfxjjyxkcaglbhfj`: Alembic was `0063`; missing `ranepa_crawl_jobs.next_attempt_at` and materialization receipt table. The crawler now checks `0066` before Chromium or student accounts; **never auto-upgrade a production database from GitHub Actions**.
- New Render Free workspace also runs another service, so keepalive is off unless GitHub repository variable `STUDGROUP_KEEPALIVE_ENABLED=true` is configured. A manual keepalive run issues just one `/ready` check. The old multi-hour push-triggered wake loop was removed.
- The scheduled RANEPA crawler and its missing-schedule watchdog are off until GitHub repository variable `RANEPA_SCHEDULE_ENABLED=true` is set. Before enabling: verified restorable backup; nonproduction clone migration test; controlled `0064` through `0066` production upgrade; Render/YDB/Telegram checks; and separate approval for recurring student account access and paid CAPTCHA budgets. Explicit manual and owner-tagged actions keep their original guards.
- Optional public repository variable `STUDGROUP_API_ORIGIN` overrides the new API origin used by keepalive and signed CAPTCHA fallback. Never point it to the suspended old Render service or store credentials in it.
- Genuine missed scheduled launches must still be investigated after schedules are enabled. An existing watchdog incident should not be closed just because checks are paused.
- A Render `live` deploy does not prove `/ready`, authenticated admin, YDB, Telegram, canaries or the six-hour timetable SLA. Verify these separately.
