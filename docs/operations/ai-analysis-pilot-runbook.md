# AI analysis shadow and pilot runbook

**Current status:** implementation is safe for offline validation; production migration,
paid DeepSeek shadow traffic and the `new` writer are not authorized by this document.
The current production worker still follows the legacy processing path and is not yet wired
to the per-group router. Therefore the transition command below is a staged operator control,
not a runnable rollout instruction, until worker dispatch and PostgreSQL tests are completed.

## Invariants

- A missing group setting routes to `legacy`.
- `shadow` keeps the legacy writer and never calls the canonical product projection.
- `new` is reachable only from `shadow`, requires the explicit G4 switch and is the sole
  writer for that group.
- Rollback changes the group to `legacy`; it does not delete canonical memory, cards,
  projection links, mode history or human changes, and it does not replay notifications.
- Raw provider responses remain controlled by `AI_STORE_RAW_PROVIDER_RESPONSES` and expire
  after 30 days. Leave this disabled outside the approved diagnostic window.

## Gates before shadow

1. Apply migrations through `0031` to a disposable PostgreSQL database and rehearse
   upgrade/downgrade. SQLite is not evidence for lock or concurrency behaviour.
2. Run the complete backend suite with `POSTGRES_TEST_URL` so the PostgreSQL-only checks run.
3. Validate and adjudicate the private held-out corpus.
4. Obtain approval for paid provider calls, the per-group shadow budget and sampling rate.
5. Confirm that production secrets and Telegram content do not appear in logs or reports.

## Controlled transitions

All changes require the group UUID, named actor and reason and are appended to
`group_analysis_mode_history`. Run from `apps/backend` with the production environment loaded.
Do not run any transition against production while the current status above says the worker
is not wired to the router.

```bash
python -m studgroup.analysis_admin \
  --group-id GROUP_UUID \
  --mode shadow \
  --actor OWNER \
  --reason "approved shadow measurement" \
  --shadow-daily-budget-usd 0.01 \
  --shadow-sample-percent 10
```

Do not run the following until G4 has been explicitly approved:

```bash
python -m studgroup.analysis_admin \
  --group-id GROUP_UUID \
  --mode new \
  --approve-g4 \
  --actor OWNER \
  --reason "G4 approved pilot"
```

Immediate rollback:

```bash
python -m studgroup.analysis_admin \
  --group-id GROUP_UUID \
  --rollback \
  --actor OWNER \
  --reason "pilot rollback"
```

## Shadow acceptance package

Report entity precision/recall, deadline/audience/kind/action accuracy, overmerge and
oversplit examples, factual consistency, completeness, readability, p50/p95 latency, token
usage, cost per 100 messages and cost per accepted entity. Numeric thresholds are approved
from the measured baseline rather than invented in advance.

The absolute acceptance invariants are zero unexplained proposal loss, zero human-lock
overwrite, zero cross-group evidence, zero duplicate product side effects and zero shadow
product writes.

## Stop and rollback conditions

Rollback immediately for any human-lock overwrite, cross-group source, duplicated card or
notification, unexplained missing proposal, unbounded cost, growing unrecoverable backlog or
student-visible internal uncertainty marker. Preserve the audit rows and collect row counts,
reason codes and hashes only; do not copy private chat text into incident logs.
