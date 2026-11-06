# StudGroup production AI operations

## Production processing (not a pilot)

Production routes are explicitly enrolled by group ID in
`AI_PRODUCTION_GROUPS` (JSON array of UUID strings). Enrollment has no time
cutoff, no global per-day / per-month token budget, and no dependency on
`release_budget_until`, `ai_recurring_approved_until`, or
`new_engine_enabled_until`.

**This authorization permits ongoing paid API requests.** It is intentional,
not a switch to turn on from a code deploy alone.

For a group to process text, its `group_analysis_settings.analysis_mode`
must be `new`, the group must be active and explicitly authorized, and
`NEW_ENGINE_ENABLED=true`. The production route ingests and processes
messages in hourly context batches around the clock, including overnight. Set
`AI_SCHEDULE_ENABLED=true` to keep related messages together without the old
00:00–07:00 Moscow blackout.

For photos, configure `IMAGE_ANALYSIS_ENABLED=true`,
`IMAGE_ANALYSIS_GROUPS` to the same authorized group UUID list,
`IMAGE_ANALYSIS_MODE=live`, and `IMAGE_ANALYSIS_SINCE` to a date covering
the first unresolved production source. `PHOTO_READING_ENABLED=false` avoids
mixing the legacy one-pass photo reader with the durable two-pass queue.
Do not silently mark unread images or attachments processed.

## Critical context invariants

- Keep the original Telegram message text, IDs, revisions, reply links and
  media-group IDs immutable in the raw source tables.
- Image pass A interprets pixels; pass B interprets images against the adjacent
  chat/album/reply context; C+ consumes the pass-B evidence.
- Captions, album members and explicit reply chains to unresolved pictures
  remain pending. Independent messages must still advance.
- If a picture is unavailable, don't fabricate its transcription or finalize
  image-dependent homework. Surface the unresolved source to operators.
- Academic item creation remains validated and deduplicated against existing
  canonical items, including deadline revisions.

## Financial and data safety still ON

Production bypasses *pilot spending caps*, not accounting or integrity:
`analysis_budget_calls` reserves a conservatively priced estimate under
the per-group SQL lock before each model HTTP call and settles actual provider
usage. No estimated cost or text prompt is shared in operator alerts.
Unknown external outcomes freeze the affected group to avoid duplicate charges;
they must be reconciled, never automatically replayed. Bounded retry counts
remain in place. Retain production ledger rows for extended audit.

## Operational startup and release

1. Confirm the intended group ID and dedicated production route; never enable
   the production group allowlist for an unrelated or unverified group.
2. Pass backend CI and deploy a verified commit to Render.
3. Merge the production environment variables (never replace existing secrets).
4. Verify Render `live`, health endpoint, queue depth decrease, completed
   model packages, image pass-A/pass-B records, and settled financial entries.
5. Reconcile older messages without re-importing Telegram history. Consider
   disabling outbound homework notifications *during historic catch-up* so
   students do not receive duplicate/expired alerts. Enable notifications
   when the backlog is drained.
6. Verify owner pause incidents recover after processing is healthy.

## 24/7 infrastructure requirement

The current StudGroup web service on Render is on the **Free** instance plan.
That plan spins down after 15 minutes with no inbound traffic; best-effort
GitHub Actions health pings are not a production uptime guarantee. Upgrade
the existing service (not a second parallel service) in Render Dashboard →
StudGroup → Settings → Instance Type → a paid always-on compute instance,
then redeploy. Review the recurring hosting charge in Render Billing. The
connected Render tool available to this project can deploy and modify
environment variables, but **cannot change an existing instance type**.
Until the plan is upgraded, the backend is operational on wake-up but cannot
truthfully be called 24/7.

## Operations watchlist

Monitor for:
- pending backlog not decreasing across consecutive worker sweeps;
- `AI_PROCESSING_STATUS` changes and `AI_BACKLOG_PAUSED` incidents;
- `paid_outcome_hold` and undecided charged requests;
- failed/stale image jobs and missing contextual evidence;
- provider balance, rate limiting, and unexpectedly fast cost growth;
- duplicate or expired homework notifications.
