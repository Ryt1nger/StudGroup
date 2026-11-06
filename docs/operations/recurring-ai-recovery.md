# Recurring AI processing and safe recovery

## Problem this prevents

A one-off 24–48h funding grant expires even when its dollar amount is not used. If a
funded group also has an unread photo and no compatible vision worker, its first
package can wait indefinitely while unrelated homework accumulates.

The original one-off `release_budget_*` fields and their payment ledger are immutable;
this feature **does not reset or extend** those grants.

## Strict source/context invariants

- Telegram messages, edits, reply-to IDs, group/chat boundaries and album IDs stay
  in the durable `raw_messages` and `message_attachments` tables.
- Vision pass A reads pixels; pass B uses nearby, reply and album context. Only
  pass-B-ready, source-revision-matching evidence can justify a vision-derived
  academic card in the C+ package.
- While a source image is unresolved, its caption, album and explicit reply chain
  remain pending. Unrelated text can progress without waiting for the image.
- A failed or budget-deferred image must not be marked "analyzed" to unblock a
  caption or reply. In a denied window, do **not** re-import already ingested
  Telegram history or manually mark rows completed.
- An academic package interrupted by a funding check is `budget_deferred`, not
  `failed`. Its original targets remain pending; the next funded iteration
  uses the existing deduplication and revision guards.

## Recurring budget policy: off by default

For a group to spend using recurring funding, an operator must explicitly set:

- `AI_RECURRING_BUDGET_GROUPS` — JSON list of group UUIDs approved to spend.
- `AI_RECURRING_APPROVED_UNTIL` — finite timezone-aware timestamp.
- `AI_RECURRING_APPROVAL_RECEIPT` — exactly 64 lowercase hex characters,
  identifying an independently approved funding decision.
- `NEW_ENGINE_ENABLED=true` for the authorized pilot group in `new` mode.

An absent group, expired timestamp, or missing receipt blocks all recurring
requests. There is no automatic approval renewal and no paid deployment action
when the policy is merely added to the code.

Default limits (UTC calendar day and UTC calendar month, **per group**):

| Guard | Daily | Calendar month |
| --- | ---: | ---: |
| All AI requests | USD 0.20 | USD 6.00 |
| Text/C+ | USD 0.10 | USD 3.00 |
| Images, including cabinet scans | USD 0.10 | USD 3.00 |

The limits may be lowered by the operator. Reservations happen under the SQL
group-settings lock before each external provider call. Each physical attempt
settles into `analysis_budget_calls` regardless of message deletion. Both
reserved and charged amounts count against each cap. Unknown paid outcomes
freeze the group; neither retry nor restart can silently erase the obligation.
A 30/31-day month cannot evade the cap because recurring entries are retained
until **45 days after the end of their creation month**.

The pre-existing per-hour package cap and independent image cap remain additional
stricter safety rails. If the daily cap runs out midway through a two-pass package,
the package/image job defers and can resume under the next permitted period.
Periodic replenishment never resets a paid-outcome safety hold.

## To enable pilot catch-up

1. Review and approve the exact group(s), spending limits and finite authorization
   expiry separately. A prior one-off grant is **not** an approval for recurring spend.
2. Confirm `image_analysis_enabled`, `image_analysis_groups`,
   `image_analysis_mode=live`, and `image_analysis_since` where picture-based
   homework must be reconstructed. The image worker is an independent two-pass
   process; do not use legacy photo reading instead while release accounting is enabled.
3. Publish the tested backend to Render. **StudGroup's Render backend has
   autoDeploy disabled**: merging to GitHub alone does not update production.
4. Apply only the operator-authorized recurring environment settings, then
   check /ready, budget counters and `AI_PROCESSING_STATUS` / `AI_BACKLOG_PAUSED`
   logs. Allow the durable queue to catch up; do not duplicate imports.
5. Verify (read-only) that `processing_state='pending'` decreases, the linked
   photo/reply evidence remains intact, and AI cards use the expected revisions.
   Pay special attention to deadlines and duplicate canonical items.
6. If `paid_outcome_hold` appears or source/evidence validation fails, stop
   automatic funding and investigate; never clear a hold without reconciliation.

## Render Free is not guaranteed hourly

The API and its embedded worker may sleep on Render Free. The repository has a
GitHub Actions health ping at off-peak ten-minute marks, but scheduled GitHub
Actions are best-effort. A guaranteed wall-clock SLA requires a persistent
worker/paid service or an independently supervised external scheduler. Money
limits alone do not wake a sleeping worker. An ordinary API request and worker
restart must be safe and idempotent, but timing can still drift.
