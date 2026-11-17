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

- Require green backend and frontend CI on final combined head SHA, including database migration upgrade from revision 0053 through 0065, Ruff/format, backend unit suite, TypeScript, frontend unit suite, production build, Playwright Chromium, and shared-types/OpenAPI generation drift.
- Verify that no merge message includes a [run-sdo-once], [run-sdo-all-once] or other paid-crawl trigger tag.
- Block merging to main while Render studgroup-frontend has autoDeployTrigger=checksPass on main. Owner authorization is needed before disabling that production setting, merging or deploying. Backend autoDeploy is off.
- Record the deployed frontend and backend SHAs and the database schema revision. Never assume main equals deployed.

## Gate 2 — controlled release (separate owner authorization)

- Separately approve migration 0064 then 0065: back up DB, test up/down on a nonproduction clone, monitor old/unproven snapshots, and do not backfill delivery receipts artificially.
- Deploy compatible schema and backend before the frontend, perform readiness/smoke checks and validate the new schedule API.
- Deploy the frontend only after confirming compatibility. Roll back or block feature enablement if the frontend expects an unknown endpoint/field.
- Canary one consenting group, then a group with two subgroups. Confirm no lost published occurrence, no cross-subgroup lesson, source-vs-recipient receipts and a failed-write backup crawl. No real student account without owner authorization.
- Confirm manual refresh only queues a request, course URL never grants Join, CAPTCHA success requires a verified schedule, and no credential or raw text appears in diagnostics.
- Measure actual six-hour coverage from oldest required week and SDO links separately; compare SDO crawler cost and product outcomes before/after. New code does NOT prove better direct-link extraction, AI accuracy, CAPTCHA recovery or a production six-hour SLA.
- Roll back if any preexisting published lesson disappears, a subgroup is exposed, deployment SHAs are incompatible or paid budget would be exceeded.

## Known unresolved external dependencies

Backend Render Free can sleep and GitHub scheduled jobs can be delayed or skipped. The separate hourly GitHub watchdog flags missing scheduled crawl launches through a deduplicated repository issue, but it shares the same GitHub scheduling platform and cannot guarantee independent always-on delivery. YDB/Postgres opaque credential reference reconciliation is not yet verified in production. Do not claim SLA success without those external checks and an owner-approved canary.
