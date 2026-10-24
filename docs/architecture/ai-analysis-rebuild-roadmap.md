# StudGroup AI analysis rebuild roadmap

**Status:** approved for implementation on 2026-10-12; implementation evidence is appended per PR.  
**Architecture:** `docs/architecture/ai-analysis-architecture.md`.  
**Baseline commit:** `d455285c13f6accc08a64468b44fb024da6b42ae`.

## 1. Delivery rules

- One logical objective per PR.
- Every behaviour change has a regression test and an observable acceptance result.
- Transaction, lock, migration and concurrency guarantees require PostgreSQL tests; SQLite
  coverage is not a substitute.
- Automated tests never call the paid provider.
- Migrations are additive until the new engine completes pilot acceptance.
- `Homework`, `AcademicDeadline`, source navigation and current OpenAPI remain compatible.
- Replay, backfill and shadow cannot create cards or notifications unless explicitly run in
  an approved writer mode.
- No PR changes commercial pricing.
- Each PR includes a release note, migration check, compatibility statement and rollback.

## 2. Dependency graph and gates

```text
G0 owner approval (complete)
  -> PR-0 baseline and accepted ADRs
  -> PR-1 silent-loss protection
  -> G1 outcome/audit approval
  -> PR-2 contracts, ports and replay
  -> G2 contract approval
  -> PR-3 source and attachment envelope
  -> PR-4 batches, cursor and discussion segments
  -> PR-5 group memory and context retrieval
  -> PR-6 AI v2 and intelligent card editor (offline/shadow only)
  -> G3 semantic policy approval
  -> PR-7 canonical academic items and resolver
  -> PR-8 compatibility projections, locks and notifications
  -> PR-9 per-group shadow comparison
  -> G4 quality/cost approval
  -> PR-10 controlled pilot and rollback rehearsal
  -> G5 owner decision to keep or revert the new writer
```

Gold-corpus annotation starts in PR-0 and continues alongside PR-1 through PR-6. It cannot
block the immediate silent-loss fix, but PR-9 cannot pass without it.

## 3. PR-0 — baseline, corpus contract and accepted ADRs

### Goal and result

Create a reproducible starting point and record the approved product decisions before
production behaviour changes. The result is a green deterministic local baseline, an
agreed evidence schema for the gold corpus and accepted ADRs for the architecture.

### Files and components

- `docs/architecture/ai-analysis-architecture.md`;
- `docs/architecture/ai-analysis-rebuild-roadmap.md`;
- accepted ADRs under `docs/decisions/` for outcome taxonomy, semantic memory/canonical
  items, deadline/type policy and shadow ownership;
- `apps/backend/tests/test_homework.py` to replace wall-clock-dependent fixture data;
- a private/ignored corpus location plus a committed schema/readme and synthetic fixtures;
- existing `diagnostics/deepseek-input-export` reports as read-only inputs.

### Database

No production migration and no data write. A disposable local PostgreSQL test database is
configured and migrated through head.

### Dependencies

Gate G0 and the ten product decisions are approved. Production access, paid provider calls
and production migrations remain outside PR-0 authority.

### Implementation steps

1. Record HEAD, worktree status, test commands and environment assumptions.
2. Stabilize the fixed-lesson test by injecting a fixed server time/source timestamp.
3. Run unit, contract, migration and PostgreSQL concurrency tests.
4. Define corpus entry schema: source IDs/revisions, entity ID, kind, field facts, evidence,
   date basis, audience, action, display title/description and negative labels.
5. Import only the supplied 500-message corpus metadata into an isolated test asset; do not
   add missing message IDs without a primary source.
6. Split prompt-development and evaluation sets by discussion/entity, not random message.
7. Record legacy replay output without paid calls; any real API baseline is a separate
   owner-approved run.
8. Perform the authorized production check only through a separately supplied read-only
   connection or export. Select only processing status, attempt status, rejection reasons
   and projection links for the two disputed sources; do not lock, replay or mutate.

### Tests and acceptance

- Entire local suite is deterministic and green.
- PostgreSQL test run is reported separately and is not silently skipped.
- `alembic upgrade head` and downgrade rehearsal run on a disposable database.
- Corpus validator rejects missing source evidence, duplicate gold entity IDs and leakage
  between development and held-out entity groups.
- Production status of 3338/3845 is verified only if the authorized read-only connection or
  export is supplied; absence of access does not block PR-0.

### Risks, compatibility and rollback

Risk is accidental inclusion of private chat text in Git. Commit only permitted fixtures,
hashes and schemas. Test/document changes do not affect runtime. Rollback is a normal revert;
no database state exists.

### Estimate

Small to medium. Corpus annotation effort is separate and depends on human review volume.

## 4. PR-1 — explicit rejection outcomes and no silent loss

### Goal and result

Correct the current pipeline without introducing the new AI strategy. Every non-empty
provider answer has a durable, queryable result. Valid empty, all-rejected, review,
publication, stale and locked paths are distinct.

### Files and components

- `apps/backend/src/studgroup/ai.py`;
- `apps/backend/src/studgroup/processing.py`;
- `apps/backend/src/studgroup/models.py`;
- `apps/backend/src/studgroup/run_reports.py`;
- `apps/backend/src/studgroup/batch_reports.py`;
- `apps/backend/src/studgroup/processing_diagnostics.py`;
- tests in `test_ai.py`, `test_processing.py`, `test_run_reports.py`,
  `test_batch_reports.py`, `test_processing_diagnostics.py` and
  `test_postgres_pipeline.py`;
- migration `0025_analysis_application_outcomes.py`.

### Database and migration

Add `analysis_application_outcomes` with:

- UUID primary key;
- attempt/job/group foreign keys;
- source revision and analysis generation;
- stage and proposal ordinal (`-1` for attempt-level summary);
- technical, academic and application statuses;
- stable reason code;
- validated source IDs and safe structural diagnostics JSON;
- provider payload SHA-256, model, prompt and schema versions;
- timestamps and `delete_at`;
- optional restricted full provider response storage, enabled only by the development/shadow
  diagnostic flag and automatically deleted after 30 days.

Use a unique key that prevents duplicate outcome rows for the same attempt/stage/ordinal.
Do not place provider bodies or Telegram text in ordinary structural outcome JSON or logs.
When the approved development/shadow trace flag is enabled, store the full provider response
in a separately access-controlled payload column/table with a 30-day `delete_at`. Aggregate
counts can remain longer without content-derived details.

### Dependencies

PR-0 baseline. It does not depend on new prompts, memory or canonical items.

### Implementation steps

1. Introduce an internal parse result containing accepted proposals, rejected descriptors,
   raw proposal count and whether the provider explicitly returned an empty list.
2. Assign stable reason codes for schema, overflow, citations, target, date-only and URL
   rejection; keep valid siblings.
3. Persist attempt summary and per-proposal outcomes in the same transaction as candidate
   and publication changes.
4. Map explicit empty to `deep_empty_valid`.
5. Map non-empty/all-rejected to `all_items_rejected`; set the raw source to
   `needs_context`, retain technical job completion separately and mark the run/report as
   completed with warnings rather than clean academic success.
6. Record accepted proposal application as published, updated, unchanged, review, locked
   or stale.
7. Correct report text so low confidence and missing deadline are not falsely reported as
   publication blockers under current behaviour.
8. Remove source text, subject and title from ordinary stdout stage telemetry.
9. Preserve every valid sibling and do not treat missing optional content as a format error.
10. Add at most one automatic repair call only for an unparseable or truncated provider
    payload. Gate it by policy, attribute its cost separately and persist both attempts. It
    must never trigger merely for low confidence, ambiguity, missing optional fields or an
    unusual but parseable academic interpretation.

### Required fixtures

| Case | Expected result |
|---|---|
| Invalid enum or required field in one proposal | `rejected_validation`; valid siblings survive; not clean empty |
| Citation outside allowed source set | `rejected_sources`; no publication |
| Explicit `assignments=[]` | `deep_empty_valid` |
| Complete homework with `deadline_at=null` | Existing publish/clarification behaviour preserved |
| Two valid assignments | Two retained outcomes/candidates; neither disappears |
| One invalid and one valid sibling | Valid sibling proceeds, invalid sibling is auditable |
| Telegram edit during provider call | `stale_revision`; no old write |
| Active human lock | `locked` or review; no overwrite |
| Transport retry | Bounded attempts, one product write, correct cost settlement |
| Duplicate workers on PostgreSQL | One effective outcome set and one application |
| Unparseable/truncated payload | At most one repair attempt; original and repair outcomes retained |
| Parseable incomplete academic content | No technical repair call; normal publication/attention/review policy |

### Acceptance

- Silent-loss invariant is zero in tests: no non-empty response disappears without a reason.
- `deep_empty_valid` and `all_items_rejected` are distinguishable in SQL and reports.
- Existing successful homework and deadline REST responses are byte-compatible where their
  generated timestamps/IDs are normalized in tests.
- No raw content appears in standard logs or structural diagnostics; full response is
  accessible only through the enabled restricted development/shadow trace and expires.
- PostgreSQL concurrency and migration tests pass.

### Risks, compatibility and rollback

The main risk is breaking reports or leaving an active run waiting on a new status. Keep
technical job states compatible and derive richer academic/application outcomes separately.
The old application can ignore the additive table. Runtime rollback deploys the previous
code and leaves the table intact; production rollback does not drop audit data.

### Estimate

Medium. The migration is small; state/report tests are the main complexity.

## 5. PR-2 — typed contracts, provider boundary and replay

### Goal and result

Separate transport, prompts, parsing, validation, normalization and publication while the
legacy strategy continues to produce the same product behaviour. Provide a network-free,
side-effect-controlled replay harness.

### Files and components

- new `studgroup/analysis/contracts.py`;
- new `studgroup/analysis/provider.py`;
- new `studgroup/analysis/legacy_strategy.py`;
- new `studgroup/analysis/validation.py`;
- new `studgroup/analysis/replay.py`;
- adapt `ai.py`, `processing.py`, `deadlines.py`, `homework_timing.py` and tests;
- keep `DeepSeekProvider` as the sole live adapter.

### Database

No new domain tables. Add schema/engine version fields to the PR-1 outcome table only if
they were not included initially. Existing attempts remain readable.

### Dependencies

PR-1 and Gate G1 approval.

### Implementation steps

1. Define versioned `AnalysisInput`, `AnalysisOutput`, `ValidationResult`, evidence/date
   DTOs and `ApplicationOutcome`.
2. Define an `AIProvider` protocol that accepts a prepared request and returns provider
   content plus usage, without domain publication methods.
3. Wrap current screen/deep behaviour in a legacy strategy adapter.
4. Replace private `_deadline_basis/_window_start/_window_end` with public DTO fields.
5. Move strict parsing and reason codes out of HTTP transport.
6. Add dependency injection for strategy and provider at the application boundary.
7. Build replay modes: validate-only, resolve-only and dry-run application. Default mode
   forbids product writes, notifications and network calls.
8. Key replay by source revision plus schema/prompt/engine version; retain provenance and
   cost attribution.

### Tests and acceptance

- Current fake-provider tests run through the port.
- Legacy request payloads and resulting product rows remain compatible.
- Parser, validator, normalizer and publisher are testable independently.
- Replay of the same fixture is deterministic and causes no network call.
- Dry-run cannot insert `Homework`, `AcademicDeadline`, `Notification` or bot outbox rows.
- A late/stale replay cannot pass the existing revision and lock guards.

### Risks, compatibility and rollback

Risk is a large mechanical refactor of `ai.py` and `processing.py`. Move one boundary at a
time and retain a compatibility adapter. No external API changes. Rollback uses the legacy
adapter and old call path; recorded outcomes remain readable.

### Estimate

Medium to large because coupling is concentrated in two large modules.

## 6. PR-3 — complete source envelope and attachment metadata

### Goal and result

Persist every supported Telegram source, including no-caption media, without claiming that
unprocessed media content is known. Provide group-local author pseudonyms to later analysis.

### Files and components

- `ingestion.py`, `models.py`, `chat_export.py`, `export_replay.py`;
- new source/attachment DTO in `studgroup/analysis/contracts.py`;
- Telegram ingestion and retention tests;
- migration `0026_message_attachments.py`.

### Database

Add `message_attachments` keyed by raw message and Telegram stable attachment identity,
including media kind, MIME/size when available, album ID, processing state and retention.
Allow a source row with empty text when it has supported attachment metadata. Do not
download files in this PR.

### Dependencies

PR-2 contracts.

### Tests and acceptance

- Duplicate delivery and edit preserve one source identity and correct revision.
- A no-caption photo/document creates a durable source plus attachment row.
- AI input shows attachment presence/state but no invented content.
- Sender sent to AI is a stable group-local pseudonym, not Telegram profile data.
- Existing text/caption behaviour remains compatible.

### Risks, compatibility and rollback

Telegram payload variants and retention are the main risks. Additive rows are ignored by
old code. Rollback disables attachment processing while retaining metadata until expiry.

### Estimate

Medium.

## 7. PR-4 — incremental batches, cursor and conversation segments

### Goal and result

Replace one-target scheduling as the future unit of analysis with versioned micro-batches
and reopenable discussion segments, while leaving legacy publication active.

### Files and components

- new `studgroup/analysis/batching.py` and `context_linker.py`;
- adapt `processing.py`, `academic_context.py`, `group_structure.py`;
- migration `0027_analysis_batches_and_segments.py`;
- unit and PostgreSQL transaction/concurrency tests.

### Database

Add `analysis_batches`, `analysis_cursors`, `conversation_segments` and
`segment_source_links` with group/version uniqueness, link reason/strength and indexes for
active segments and source revision lookup.

### Dependencies

PR-3. Legacy jobs continue unchanged; new structures initially run as dry-run metadata.

### Tests and acceptance

- Reply and topic links are deterministic and group-isolated.
- Same hashtag/time alone does not force a merge.
- A late reply or edit reopens the correct segment version.
- Re-running the same batch creates no duplicate links.
- Cursor does not advance past deferred/failed source revisions.
- Context size is bounded and reproducible.

### Risks, compatibility and rollback

Overmerge, oversplit and hot indexes are the main risks. Feature-disable the segment builder;
legacy jobs do not depend on the new tables. No product writes occur.

### Estimate

Large.

## 8. PR-5 — structured group memory and context retrieval

### Goal and result

Add evidence-backed group knowledge and a deterministic context assembler that selects only
relevant memory, active segments and existing item candidates.

### Files and components

- new `studgroup/analysis/memory.py`, `context.py`, `knowledge_policy.py`;
- adapt schedule/group-structure readers;
- migration `0028_group_knowledge.py`;
- retention, isolation and context snapshot tests.

### Database

Add `group_knowledge_facts` and fact evidence/history. Keep proposed and verified statuses,
validity intervals and verifier/source. Do not duplicate full chat text.

### Dependencies

PR-4. The item candidate portion initially reads legacy `Homework`/`AcademicDeadline`
through a typed adapter.

### Tests and acceptance

- A proposed alias cannot become verified without the configured evidence transition.
- Memory from another group never enters a request.
- Deleted/expired raw text is not reproduced from an unrestricted summary.
- The same database snapshot produces the same selected context.
- A late clarification selects the related item/context without replaying the full history.

### Risks, compatibility and rollback

Stale false memory is the primary risk. All model updates are proposals and memory can be
disabled independently. Legacy product state remains authoritative.

### Estimate

Large.

## 9. PR-6 — Context Understanding, extraction v2 and intelligent card editor

### Goal and result

Implement the new semantic strategy against versioned segments and memory, but keep it
offline/shadow-only. Produce structured facts, evidence and safe display proposals.

### Files and components

- new `studgroup/analysis/context_strategy.py`;
- new `studgroup/analysis/extraction_strategy.py`;
- new `studgroup/analysis/card_editor.py`;
- versioned prompt resources and schemas;
- DeepSeek adapter updates without changing product publication;
- fixture/gold evaluation harness.

### Database

Use analysis outcomes and segment versions. Add a shadow proposal table only if outcomes
cannot store the required typed result without violating retention; it must be isolated from
product tables and have a documented TTL.

### Dependencies

PR-5. It may begin prompt-fixture work earlier, but cannot merge before contracts/context are
stable.

### Tests and acceptance

- Homework without hashtag/topic can be found through related discussion evidence.
- One compound homework produces one item with complete content blocks.
- One long message may produce multiple independent task and deadline proposals; valid
  siblings never block each other.
- Product routing uses only task and deadline destinations. Test, credit, exam, control
  point and similar meanings are preserved in semantic title/content rather than becoming
  extra user sections.
- Display title is action-oriented and not a raw-chat placeholder.
- Description preserves all evidenced pages, numbers, quantities and restrictions.
- New unsupported numbers, dates, actions, subject or submission format fail validation.
- Partial but actionable content remains publishable; optional unknowns stay unknown.
- Minor uncertainty produces a headman-only attention reason. Only serious unresolved
  conflict/material ambiguity produces a held review proposal.
- A factual deterministic display fallback is used only when AI display text is missing,
  malformed or factually invalid, and is marked for headman attention.
- Explicitly different subgroup assignments produce separate proposals. Configured split subjects
  are filtered by membership after projection; unresolved targeting stays visible to all and enters
  editor review.
- Prompt-injection text cannot alter schema, system policy or allowed source IDs.
- Repeated unchanged input produces semantically stable display fields.
- No product or notification row is written.

### Risks, compatibility and rollback

Hallucination and token growth are primary risks. Keep strict schemas, bounded contexts,
critical-fact checks and shadow-only execution. Disable the v2 strategy without touching
legacy processing.

### Estimate

Large.

## 10. PR-7 — canonical academic items, evidence and resolver

### Goal and result

Create stable academic identity and transactionally resolve create/update/correct/
reschedule/cancel/no-change/review actions without yet changing the public API.

### Files and components

- new `studgroup/analysis/entities.py`, `resolver.py`, `evidence.py`;
- adapters for current homework/deadline candidates;
- `headman_domain.py` lock integration;
- migration `0029_canonical_academic_items.py`.

### Database

Add `academic_items`, `academic_item_evidence`, `academic_item_revisions` and projection
links. Backfill existing cards as canonical baselines without publication or notification.
Preserve current IDs through the projection link rather than replacing them.

### Dependencies

PR-6 schemas and Gate G3 policy approval.

### Tests and acceptance

- One assignment plus two clarifications remains one canonical item.
- Different assignments with the same subject/date do not merge.
- Field evidence reconstructs subject, content, date and audience origins.
- Schedule-inferred homework dates point to the next reliable lesson of the same subject,
  remain distinguishable from explicit dates and can be recalculated on schedule change.
- Separate subgroup-labelled items do not merge merely because subject and date match.
- Stale segment/item revision and human lock prevent automatic mutation.
- Backfill creates no notifications and changes no current REST representation.

### Risks, compatibility and rollback

False merging and backfill side effects are release blockers. Canonical tables remain
shadow/read-only on rollback; legacy rows and human decisions are never deleted.

### Estimate

Extra large.

## 11. PR-8 — compatibility projections and downstream effects

### Goal and result

Project approved canonical actions into existing product models atomically while preserving
API, locks, source navigation and notification idempotency.

### Files and components

- new `studgroup/analysis/publication.py`;
- adapt `processing.publish`, `notifications.py`, `homework.py`,
  `academic_deadlines.py`, `headman_domain.py`, `reviewed_publication.py`;
- OpenAPI compatibility tests; update contracts only if an additive field is approved.

### Database

Use PR-7 projection links. Add notification/item revision uniqueness only if existing event
keys cannot represent canonical revisions safely.

### Dependencies

PR-7.

### Tests and acceptance

- Existing REST schemas remain compatible.
- Generated display fields populate current title/description only after validation.
- Students receive neutral schedule-inferred wording and no technical uncertainty badge;
  the headman view can expose source basis and `needs_attention` reason.
- Configured subgroup cards are filtered by the member's per-subject choice; unanswered members see
  all variants and editors always see all variants.
- Projection and outbox records commit atomically.
- Update/reschedule/cancel recalculates future notifications without duplicates.
- Explicitly personal content cannot trigger a group-wide card or push. Unknown audience
  follows the serious-ambiguity review policy; an explicit subgroup label is publishable
  only when it resolves to a configured subgroup; otherwise the card requires editor review and
  remains visible to all.
- Manual corrections remain authoritative in both canonical and projected views.

### Risks, compatibility and rollback

Dual state drift is the main risk. Reconciliation checks compare canonical and legacy rows.
The new publisher remains disabled until shadow acceptance. Rollback selects legacy writer
and preserves existing projected cards.

### Estimate

Large.

## 12. PR-9 — per-group shadow mode and comparative evaluation

### Goal and result

Run legacy and new analysis on the same permitted inputs, with the new engine unable to
produce product side effects. Generate entity-level quality, display-quality, cost and
latency comparisons.

### Files and components

- new `studgroup/analysis/routing.py`, `shadow.py`, `evaluation.py`;
- worker/runtime configuration and reports;
- migration `0031_group_analysis_shadow.py`;
- operational and privacy tests.

### Database

Add group-scoped `analysis_mode=legacy|shadow|new`, engine version and shadow budget. Isolate
shadow batches/outcomes from legacy checkpoints.

### Dependencies

PR-8. Gold corpus and evaluation matching must be ready.

### Tests and acceptance

- Shadow cannot insert/update product, notification, bot-outbox or subscription rows.
- Legacy and shadow receive the same allowed source snapshot and retention boundary.
- Comparison matches unique items and actions, not raw proposal counts.
- Metrics cover entity recall/precision, date/audience, update accuracy, overmerge/oversplit,
  display factual consistency/completeness/readability, cost and p50/p95 latency.
- Silent loss, manual override, cross-group leakage and duplicate side-effect counts are zero.
- Numeric quality and cost thresholds are proposed from measured baseline and approved at G4;
  they are not invented in advance.

### Risks, compatibility and rollback

Shadow consumes real tokens and may double cost. Apply a separate cap and sampling plan.
Turning shadow off stops new work and leaves artifacts for permitted analysis; legacy remains
the only writer.

### Estimate

Medium to large plus observation time.

## 13. PR-10 — controlled pilot switch and rollback rehearsal

### Goal and result

Enable the new writer for one approved pilot group after Gate G4, monitor it, rehearse
rollback and produce the evidence package for Gate G5. This PR does not remove legacy code.

### Files and components

- routing and operator controls;
- deployment/runbook documentation;
- health, backlog, cost and outcome dashboards/reports;
- end-to-end PostgreSQL and permitted Telegram-chat tests.

### Database

No destructive migration. Only the owner-controlled group mode changes. New canonical data
and projection links remain compatible with a return to legacy.

### Dependencies

PR-9 and explicit Gate G4 approval.

### Tests and acceptance

- `legacy -> shadow -> new -> legacy` is rehearsed on a test group/database.
- Only one writer owns each group in every mode.
- A live message/edit produces the correct source, item/projection and source link.
- Retry, outage, budget defer, edit race and human correction produce no duplicate card or
  notification.
- Rollback preserves user cards and human changes and does not replay old notifications.
- Pilot evidence includes quality, display, cost, latency, backlog, review load and incidents.
- Owner explicitly decides at G5 whether the group remains on `new`.

### Risks, compatibility and rollback

Operational quality and cost are the main risks. Immediate rollback is a group-mode switch
to legacy. Canonical memory becomes read-only; no schema downgrade is required.

### Estimate

Medium implementation plus an owner-defined observation period.

## 14. Post-PR-10 backlog

The following are separate product slices, not hidden inside the core rollout:

- OCR/vision for photos, then documents/PDFs and albums, each with cost and retention caps;
- complete event vocabulary and approved UI mapping;
- conditional audiences beyond the implemented per-subject subgroup membership;
- revised reminder cadence and morning digest;
- broader multi-group scale/fairness testing;
- eventual legacy schema/code retirement after a separate decision.

## 15. Quality and cost evidence package

Before Gate G4, produce:

1. versioned corpus manifest and annotation instructions;
2. inter-review disagreement log and adjudicated gold entities;
3. held-out evaluation results for legacy and shadow on identical inputs;
4. entity-level match report with false-positive, false-negative, overmerge and oversplit
   examples;
5. field report for content, date, audience, action and evidence;
6. display report for factual consistency, critical-fact completeness, readability and
   stable formatting;
7. token/cost report per 100 messages, unique item and group period;
8. latency and urgent-path report;
9. PostgreSQL race/idempotency/lock/migration results;
10. privacy and retention review;
11. rollback rehearsal record.

Absolute release invariants are zero unexplained proposal loss, zero human-lock overwrite,
zero cross-group context leak and zero duplicate product side effects. Other numeric
thresholds are set only after baseline measurement and owner approval.

## 16. Immediate implementation checklist after approval

Only after the owner approves Gate G0:

1. create a clean feature branch/worktree without overwriting current uncommitted files;
2. implement PR-0 baseline stabilization and proposed ADRs;
3. present PR-0 evidence and final PR-1 migration shape;
4. implement PR-1 with no new prompt or paid API call;
5. run the full local and disposable-PostgreSQL suites;
6. deliver the PR-1 acceptance matrix, migration output, privacy check and rollback note;
7. stop at Gate G1 for owner approval before contract refactoring.

No later PR, paid benchmark, production migration or rollout is implied by approval of the
architecture alone.

## 17. Implementation evidence — 2026-10-12

### PR-0

- Gate G0 and all ten product decisions are recorded as approved.
- ADR-006 through ADR-009 record C+, memory/evidence, publication and diagnostics policy.
- The wall-clock-dependent subject-page test now uses a fixed source time.
- A private-corpus contract, synthetic multi-item fixture and validator cover evidence,
  duplicate identities, negative labels and development/evaluation leakage.
- Ruff passes. All 367 collected tests pass when executed per module, except the existing
  PostgreSQL-only test that is skipped without `POSTGRES_TEST_URL`.
- A disposable SQLite migration rehearsal reaches the historical PostgreSQL-specific
  constraint migration and cannot replace the required PostgreSQL rehearsal.

### PR-1 through PR-8

- Durable technical/academic/application outcomes distinguish valid empty, rejected sibling,
  stale, retry and failed processing; a single repair is restricted to malformed/truncated JSON.
- Versioned C+ contracts, replay, attachments, batches, discussion segments, structured memory,
  evidence validation, intelligent card editing, canonical items and guarded compatibility
  projections are implemented.
- One Telegram message can project several tasks/deadlines. Explicit subgroup cards remain
  separate and labelled; personal/unknown audience is blocked from group publication.
- Schedule-inferred and minor-attention states are visible to headman/deputy but mapped to the
  neutral existing state for students. Manual locks and expected revisions block AI overwrite.

### PR-9

- Per-group `legacy|shadow|new` settings default safely to legacy and enforce exactly one writer.
- Shadow sampling and daily budget authorization are separate from legacy spend.
- Strict Context Understanding output, evidence checks, two-pass shadow execution, 30-day
  operational summaries and entity/display/cost/latency evaluation helpers are implemented.
- No production shadow run or paid provider call has been performed; Gate G4 remains open.
- The existing worker does not yet dispatch through the per-group router. Until that integration
  is implemented and tested, the settings and shadow executor are offline/staged components.

### PR-10 implementation and rehearsal status

- Mutation proposals now carry explicit `create|update|reschedule|cancel`, canonical item ID and
  expected revision; accepted proposal ordinals survive partial rejection without collisions.
- The canonical writer emits one outcome for every accepted or rejected proposal and projects
  through the compatibility boundary.
- Audited mode transitions prohibit `legacy -> new`; `shadow -> new` requires an explicit G4
  switch. Emergency rollback preserves product rows and history.
- Ruff passes and the complete local backend suite reports **422 passed, 1 skipped**. The skip is
  the PostgreSQL-only test because no disposable `POSTGRES_TEST_URL` is available.
- PostgreSQL SQL generation for migrations 0029–0033 succeeds. A real PostgreSQL migration,
  concurrency run, held-out gold evaluation, paid shadow observation and production pilot are
  still required. Runtime/Celery dispatch into the new router is also still required. Therefore
  the result is **not approved for production `shadow` or `new` mode**.

### Integration follow-up

- Celery and embedded runtime enter through the analysis dispatch boundary. Legacy claiming
  excludes groups marked `new`, preventing dual publication while the new worker path is absent.
- A disposable PostgreSQL 16.15 server completed upgrade/downgrade/re-upgrade through 0031.
  The complete suite passed with **423 passed, 0 skipped** including the concurrent claim test.
- C+ shadow execution is not yet connected to worker dispatch, and `new` groups are intentionally
  left queued. Current verdict remains **offline-ready**, not shadow-ready.
