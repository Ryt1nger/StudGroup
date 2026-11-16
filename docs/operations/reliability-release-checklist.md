# Reliability release gate — 2026-11-16

## Gate 1 — review and CI (no deployment)

- Require green backend and frontend CI on final combined head SHA, including database migration upgrade from revision 0053, unit and browser tests, and shared-types generation drift.
- Verify that no merge message includes a [run-sdo-once], [run-sdo-all-once] or other paid-crawl trigger tag.
- Block merging to main while Render studgroup-frontend has autoDeployTrigger=checksPass on main. Owner authorization is needed before disabling that production setting, merging or deploying. Backend autoDeploy is off.
- Record the deployed frontend and backend SHAs and the database schema revision. Never assume main equals deployed.

## Gate 2 — controlled release (separate owner authorization)

- Deploy compatible schema and backend before the frontend, perform readiness/smoke checks and validate the new schedule API.
- Deploy the frontend only after confirming compatibility. Roll back or block feature enablement if the frontend expects an unknown endpoint/field.
- Canary one consenting group, then a group with two subgroups. Confirm no lost published occurrence and no cross-subgroup lesson.
- Confirm manual refresh only queues a request, course URL never grants Join, CAPTCHA success requires a verified schedule, and no credential or raw text appears in diagnostics.
- Measure actual six-hour coverage from oldest required week and SDO links separately; compare SDO crawler cost and product outcomes before/after.
- Roll back if any preexisting published lesson disappears, a subgroup is exposed, deployment SHAs are incompatible or paid budget would be exceeded.

## Known unresolved external dependencies

Backend Render Free can sleep and GitHub scheduled jobs can be delayed or skipped. An independent always-on missing-slot alert and YDB/Postgres opaque credential reference reconciliation are not yet verified in production. Do not claim SLA success without those external checks and an owner-approved canary.
