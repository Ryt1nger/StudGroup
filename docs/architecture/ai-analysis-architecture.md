# StudGroup AI analysis architecture

**Status:** approved product architecture; implementation authorized on 2026-10-12.  
**Date:** 2026-10-12.  
**Code baseline:** `d455285c13f6accc08a64468b44fb024da6b42ae`.  
**Scope:** Telegram source capture, discussion context, AI analysis, evidence validation,
academic entity resolution, publication projections, review, audit, cost and rollout.

## 1. Decision summary

StudGroup should use an evolutionary **C+ architecture**. The existing Telegram webhook,
PostgreSQL source of truth, Celery/worker integration, revision/generation guards, budget
reservation, human locks and WebApp contracts remain in place. The system replaces the
target-centred context and tightly coupled AI/application path with versioned contracts,
discussion segments, structured memory, evidence-backed proposals, canonical academic
items and an explicit application outcome.

The transition is additive. The new engine is evaluated in shadow mode first and cannot
write `Homework`, `AcademicDeadline`, notifications or other user-visible state until the
quality and rollback gates are approved. Legacy and new engines must never be simultaneous
writers for the same group.

The first implementation change is not a new prompt. It is a bounded correction of the
current silent-loss path so that a non-empty provider answer cannot become an unexplained
successful empty result.

## 2. Sources and authority

The following inputs were reconciled:

1. executable code, migrations and tests at the baseline commit;
2. `StudGroup_AI_Analysis_Rebuild_Codex_TZ-2.md`, version 1.1, which is newer than the
   same-named version 1.0 download;
3. `StudGroup_Analysis_Core_Rebuild_Plan.md`;
4. `docs/product/module-0-domain-rules.md`, `module-1-technical-foundation.md`,
   `mvp-implementation-boundaries.md` and existing ADRs;
5. `docs/architecture/ai-analysis-module.md` and the local diagnostic export;
6. the read-only technical audit performed on 2026-10-12.

Executable code has highest authority for current behaviour. Approved product documents
have authority over product rules. The new specification and this architecture describe
the proposed future. Where they conflict with an approved rule, the conflict remains open
until the product owner decides it.

No production database was available. The production state of Telegram messages 3338 and
3845 is therefore not verified. The historical probe result must not be treated as a trace
of the current target-specific production path.

## 3. Verified current baseline

### 3.1 Preserved foundations

| Foundation | Current evidence | Decision |
|---|---|---|
| Authenticated, idempotent Telegram ingress | `ingestion.webhook`, `ReceivedUpdate`, pilot authorization | Preserve |
| Durable source and edit versioning | `RawMessage`, `revision`, `analysis_generation`, original timestamp | Preserve and extend |
| PostgreSQL job ownership | `AIJob`, leases, unique source revision/generation | Preserve |
| Stale-result protection | revision check before applying provider result | Preserve as an invariant |
| Human precedence | `HeadmanDecision` and `ai_locked` | Preserve as an invariant |
| Budget and incident guards | reservations, usage settlement, circuit breaker, retry state | Preserve; improve attribution |
| Date normalization | source timestamp and group timezone, backend verification | Preserve behind a typed port |
| Existing product projections | `Homework`, `AcademicDeadline`, `AICandidate` | Preserve as compatibility projections |
| Existing WebApp API | `/today`, `/homework`, `/deadlines`, source navigation | No breaking changes during migration |

### 3.2 Verified limitations

- Per-item validation can reject every proposal and return a valid empty `BatchResult`.
  The orchestrator then closes the job and source as completed without a candidate.
- `completed` conflates light-negative, valid deep-empty, rejected content, review-only and
  published results.
- `AIAttempt.metrics` contains counters, not a durable per-proposal application history.
- `GroupAIProfile` is a deterministic structural map, not semantic memory.
- AI context is bounded and target-centred; it does not persist discussion identity.
- `sender_id` is stored but not included in model context.
- A media message without text or caption is not persisted as `RawMessage`.
- `Homework` and most deadline identity are anchored to one raw message, so clarification
  and deduplication depend on heuristics rather than a stable academic entity.
- Multiple valid assignments are retained as review candidates, but none is independently
  published.
- Hosted stage telemetry can contain a source-text excerpt and generated academic text.
- There is no per-group `legacy|shadow|new` execution mode.

### 3.3 Test baseline

On 2026-10-12 the backend suite produced 359 passed, 1 failed and 1 skipped tests. Ruff
passed. The skipped test is the opt-in PostgreSQL concurrency test because
`POSTGRES_TEST_URL` was not configured. The failure is a time-dependent subject-page test:
it creates a source at the wall clock time but queries a fixed lesson on 2026-10-12, so it
fails after that lesson begins. This baseline defect must be removed before quality changes
are compared.

## 4. Reconciliation table

| Topic | Current code or approved rule | New specification/plan | Resolution status | Recommended resolution |
|---|---|---|---|---|
| Subscription price | An existing product document contains a historic 599 RUB value | v1.1 says price is not approved; 499/599 are only scenarios | Resolved by current owner request | Do not mention a fixed price or change commercial logic |
| Homework without an explicit date | Module 0 permits a next-lesson inferred deadline when schedule coverage is reliable | Plan says keep the date unknown and do not invent it | Approved | Use the next reliable lesson of the identified subject as `schedule_inferred`; students see neutral “к следующему занятию”, while only the headman sees that it was not confirmed by a message |
| Reminder timing | Module 0 currently says 24 h/2 h and 3 d/24 h by event type | Rebuild documents mention D-2/D-1 in places | Resolved by scope | Preserve current behaviour; reminder timing is outside the analysis rebuild and requires a later notification ADR before any change |
| Academic type vocabulary | Product: `control`, `test`, `exam`; code: `control_point`, `assessment`, `test`; proposal adds more types | Three incompatible vocabularies | Approved | The product has two destinations: task and deadline. Semantic labels such as test, credit, exam or control point drive generated content, not separate user sections |
| Confidence threshold | Product document describes 85/65 thresholds | Current code publishes a complete low-confidence homework as `needs_clarification` | Approved | Automatic publication is the default. Review is triggered by a serious conflict, material ambiguity, explicit model uncertainty or a human lock—not by confidence alone |
| Valid empty deep response | Current code treats it as normal completion | Specification requires it to be distinct from all-items-rejected | Technical decision | Preserve it as valid, record `deep_empty_valid`, and never infer provider failure from emptiness alone |
| Raw provider response retention | Current pipeline does not retain it | Specification asks for safe audit but leaves privacy policy open | Approved for development/shadow | Retain the full response behind a restricted flag for 30 days during development and shadow; disable it after diagnostics stabilize |
| Semantic card text | Current `title` and `description` come directly from extraction without a fact/display boundary | v1.1 requires an intelligent editor, structured descriptions and no invented facts | Approved | AI produces the normal semantic title and structured description. An evidence-only fallback is a last resort and adds a headman-only attention marker |
| Grouping unit | Current engine analyses one target with a bounded neighbourhood | New plan uses batches and discussion segments | Technical decision | Incremental batch creates/reopens segments; extraction runs on relevant segment versions |
| Canonical entity order | Plan places canonical items after AI v2 | New AI needs existing-item identity for update/reschedule/cancel | Technical adjustment | Define item DTO and resolver port before AI v2; create the database-backed canonical model after shadow schemas are stable |
| Shadow mode | No per-group mode exists | New engine must start in shadow | Technical decision | Add a group-scoped mode; only legacy or new may own product writes, shadow never writes them |
| Media | No-caption media is not stored | Reliable source capture is required; vision is later | Technical decision | Persist attachment metadata early; do not claim content understanding until a separate media extractor exists |
| Missing IDs 4029/4043/4063/4067/4124 | They are absent from the supplied 500-message archive | Earlier plan suggested adding them to the corpus | Corrected | Add only from a verifiable primary source with consent; never reconstruct from memory |
| Plan reference `README(1).md` | File is not present in the repository | Listed as a source in the draft plan | Obsolete reference | Remove it from the approved source list |
| Old pilot status | `pilot-vertical-slice.md` contains a 2026-10-05 implementation snapshot | Current code already implements more of the pipeline | Historical, not normative status | Preserve its vertical-slice principle, not its old completion status |

## 5. Architectural principles

1. Source content is untrusted data, never an instruction to the agent or backend.
2. No proposal may affect product state without valid group, source, source revision and
   evidence references.
3. Technical completion, academic interpretation and product application are separate.
4. Unknown data remains unknown. Deterministic inference is labelled with its basis.
5. Generated display text is a presentation proposal, not evidence.
6. One academic item can have many source messages and revisions.
7. Human-confirmed fields and locks outrank AI proposals.
8. All processing is incremental, group-isolated, idempotent and bounded.
9. Shadow evaluation has no product or notification side effects.
10. Token savings must come from bounded delta context and reusable structured state, not
    from irreversible filtering.

## 6. Logical architecture

```text
Telegram update
  -> Source ingestion
  -> RawMessage revision + Attachment metadata
  -> Incremental batch/change detector
  -> Conversation linker and active segments
  -> Context assembler (delta + selected memory + related items)
  -> Context Understanding
  -> Academic Extraction and card-editing proposal
  -> Parser and evidence validator
  -> Date/subject/audience normalizers
  -> Entity resolver and lock/conflict guard
  -> ApplicationOutcome
  -> Canonical AcademicItem + evidence + revision history
  -> Compatibility projection
  -> Homework / AcademicDeadline / AICandidate
  -> Existing REST/WebApp and idempotent notifications
```

### 6.1 Source ingestion

Retain the current webhook, update deduplication and group authorization. Extend the source
model so every supported Telegram message can be represented even when it has no text:

- stable group and Telegram message identity;
- original timestamp, edit timestamp and source revision;
- sender represented internally and as a group-local pseudonym for external AI;
- reply and topic metadata when Telegram supplies it;
- zero or more attachments with Telegram `file_id`/`file_unique_id`, media kind, MIME type,
  size, caption presence and album/media-group identity;
- source availability and retention state.

Capturing an attachment does not mean understanding it. Until OCR/vision completes, its
content state is `unprocessed|unsupported|failed|available`, and no fact may be inferred
from the unseen bytes.

### 6.2 Incremental scheduler

The scheduler selects new, edited and newly connected source revisions. Its idempotency key
includes group, source revision, processing generation, batch version and engine version.
It keeps a per-group cursor but can reopen a previous segment after an explicit reply, an
edit or a candidate match to an active canonical item.

Budget exhaustion creates `deferred_budget`; it does not advance the semantic cursor past
unprocessed data. SQL remains authoritative. Redis/Celery may wake workers but never owns
the only copy of work.

### 6.3 Conversation context engine

The engine first applies deterministic links:

- reply chain and topic identity;
- explicit edit/repost/source relation;
- existing item evidence;
- attachment album identity.

It then ranks hypotheses using bounded time distance, group-local author pseudonym,
subject aliases, explicit numbers/pages, correction language and active segment state.
Every link records `reason`, `strength` and whether it is structural, deterministic or an
AI hypothesis. Time proximity or a shared hashtag alone cannot prove identity.

A segment may contain zero, one or many academic items. It has a version, active/closed
state, last analysed version, TTL and reopen reason. Context assembly is deterministic for
the same database snapshot and contract version.

### 6.4 Structured memory

Memory is divided into four stores:

1. `GroupKnowledgeFact`: verified or proposed aliases, subjects, teachers, schedule facts
   and group conventions, with evidence, validity interval, status and verifier.
2. `ConversationSegment`: short-lived active discussion state and unresolved links.
3. `AcademicItem`: stable academic entity with current structured fields.
4. `AcademicItemRevision` and evidence history: append-only changes, actor, old/new values,
   field evidence and downstream effects.

Free-text summaries may help the model but cannot be authoritative for dates, audience,
item kind, source identity or human verification. Proposed memory updates remain proposals
until deterministic or human confirmation.

### 6.5 Two AI passes

#### Context Understanding

Input is a micro-batch of new/changed messages plus bounded related context. Output proposes:

- segment links and split/merge hypotheses;
- relevance state: irrelevant, relevant, ambiguous, continuation, correction,
  cancellation or academic link;
- existing items that may be affected;
- whether deep extraction is required;
- missing context or attachment needs;
- proposed memory updates with evidence.

This pass is high-recall and reversible. `irrelevant` is not a permanent deletion: later
replies or item links can reopen it.

#### Academic Extraction and intelligent card editing

Input is a versioned discussion segment, trusted memory slice, related items, source times,
timezone and optional verified schedule context. Output contains zero or more proposals:

- action: create, enrich, correct, reschedule, cancel, reopen, no-change or review;
- kind, subject, audience and lifecycle intent;
- structured task facts and content blocks;
- raw date phrase, reference source/time, date basis and uncertainty;
- field-level evidence;
- `display_title` and `display_description` as presentation proposals;
- conflicts, unknowns and review reason;
- model, prompt and schema versions plus usage.

### 6.6 Intelligent card editor

The model acts as an editor after extracting facts, not as a free-form copywriter.

The canonical content representation uses structured blocks such as:

```text
TaskContentBlock
  action: solve | read | answer | prepare | submit | bring | review | other
  object_text
  exercise_numbers[]
  pages[] or page_range
  quantity?
  constraints[]
  evidence_refs[]
```

`display_title` is short, specific and action-oriented. `display_description` is coherent
Russian prose or a compact list containing every material requirement. Chat greetings,
repetition, profanity and irrelevant discussion are removed. Original wording is retained
only in source evidence.

Before publication the backend checks:

- every number, page, quantity, date, subject and audience restriction against evidence or
  a labelled deterministic derivation;
- preservation of all critical structured facts in the display text;
- absence of unsupported deadlines, submission formats, mandatory wording or actions;
- stable output for an unchanged item version;
- manual field locks.

Deterministic checks cannot prove all semantic equivalence. A display proposal that fails
critical-token coverage, contradicts evidence or adds unsupported facts is rejected or sent
to review without deleting the underlying academic proposal. A safe formatter may produce
a temporary evidence-based fallback, but it cannot invent missing semantics and does not
replace evaluation of the intelligent editor.

One assignment with several parts becomes one card with several content blocks. Independent
assignments become separate item proposals. Split/merge decisions carry reasons and are
measured explicitly in the gold corpus.

One source message or discussion may produce zero, one or many cards in any combination of
the two product destinations. A long message containing three assignments and one
control-point date produces three task cards and one deadline card; it is never forced into
a single card merely because it has one Telegram message ID.

### 6.7 Validation and outcomes

Parsing returns both accepted and rejected proposal records. It never mutates a non-empty
provider response into an indistinguishable empty success.

Three state families are stored separately:

| Family | Examples |
|---|---|
| Technical attempt | received, parsed, transport retry, incomplete output, failed |
| Academic interpretation | light negative, deep empty valid, proposals found, ambiguous, all rejected |
| Product application | published, updated, unchanged, review required, stale, locked, deferred, failed |

Every rejected proposal has a stable reason code, attempt link, source scope and safe
diagnostic shape. A correct empty response is `deep_empty_valid`; a response containing only
invalid proposals is `all_items_rejected`. Neither is inferred from the number of published
cards.

### 6.8 Entity resolver

The resolver searches candidates deterministically by group, explicit source/reply links,
existing evidence, kind, subject and active time horizon. Semantic comparison is used only
for ambiguous candidates. The final action is guarded transactionally by:

- source revision and segment version;
- canonical item revision;
- human field locks;
- single-writer engine ownership;
- uniqueness/idempotency key;
- audience and conflict policy.

Overmerge is treated as seriously as duplication. Same subject and due date are not enough
to merge assignments. A confirmed manual item is never changed automatically; an AI
contradiction becomes review evidence.

### 6.9 Publication adapter

The canonical model projects to current `Homework`, `AcademicDeadline` and `AICandidate`
tables. Existing REST shapes remain authoritative during the migration. Projection and its
notification outbox change are atomic and idempotent. Shadow mode bypasses this adapter.

The public `title` and `description` receive approved display fields, while source excerpts,
links and provenance remain separate. A rollback to legacy keeps already valid user cards
and human corrections; it does not replay old notifications.

Publication has three product levels:

1. normal automatic publication with no marker;
2. automatic publication with a headman-only `needs_attention` marker and concise reason
   when the interpretation is useful but has a minor uncertainty;
3. held for headman review only for a serious unresolved conflict, material ambiguity,
   explicit model inability to choose a reliable interpretation, or a protected human lock.

Students never see technical uncertainty badges. Partial but actionable cards are visible;
unknown optional fields remain empty. A card is held only when a student could not
understand the required action/event or when publishing it could materially mislead.

Subgroups use a deliberately limited first version. If the source explicitly gives
different assignments for subgroup 1 and subgroup 2, the engine creates separate cards
with visible subgroup labels. Both remain visible to the whole group until membership and
personal filtering are implemented later. A general assignment has no subgroup label.

## 7. Proposed internal contracts

### 7.1 AnalysisInput

```text
schema_version
engine_version
batch_id, group_key, timezone, analysis_mode
cursor_from, cursor_to
messages[]:
  source_id, telegram_message_id, revision, sent_at
  author_key, reply_to, topic, text
  attachments[] with content_state
selected_memory[] with status/evidence/version
open_segments[] with version and link reasons
relevant_items[] with canonical revision and locked fields
schedule_context with coverage and trust state
```

External requests use internal stable keys, not Telegram group IDs or unnecessary personal
profiles. Unknown, hypothesised and verified values are distinguishable.

### 7.2 AnalysisOutput

```text
schema_version, batch_id, segment_versions
context_decisions[]
proposals[]:
  proposal_id, action, kind
  structured subject/content/date/audience fields
  content_blocks[]
  display_title, display_description
  field_evidence{}
  existing_item_ref?, uncertainties[], conflicts[]
memory_update_proposals[]
diagnostics, model_version, prompt_version, usage
```

### 7.3 ApplicationOutcome

```text
outcome_id, attempt_id, proposal_id?
technical_status
academic_status
application_status
reason_code
source_refs, segment_version, item_ref?, item_revision?
side_effects: projection IDs and notification/outbox IDs
schema/model/prompt/engine versions
created_at, delete_at
```

Reason-code families include schema, source, revision, evidence, date, audience, conflict,
lock, duplicate, budget, transport and policy. User-visible statuses are mapped separately;
an internal rejection code is not automatically a public card.

## 8. Proposed data model

The final names are approved in PR design; not every table must be created at once.

| Entity | Purpose | Retention notes |
|---|---|---|
| `AnalysisApplicationOutcome` | Per-attempt/per-proposal audit and reason codes | Detailed structural diagnostics have a 30-day TTL |
| restricted provider trace | Full provider response for development/shadow diagnostics | Feature-flagged, technical-admin only and automatically deleted after 30 days |
| `AnalysisBatch` / cursor | Incremental source range and engine ownership | Retain operational history without raw duplication |
| `ConversationSegment` | Active/closed discussion identity and versions | Active TTL plus compact closed state |
| `SegmentSourceLink` | Source revision, role, link reason and strength | Follows source/evidence policy |
| `GroupKnowledgeFact` | Versioned verified/proposed knowledge | Validity and evidence required |
| `AcademicItem` | Stable item identity and current fields | Product retention policy |
| `AcademicItemEvidence` | Field-specific provenance | Mark unavailable after permitted source deletion |
| `AcademicItemRevision` | Append-only human/AI change history | Product retention policy |
| `AcademicProjectionLink` | Canonical item to legacy projection | Needed for rollback and compatibility |
| `GroupAnalysisSettings` | `legacy|shadow|new`, engine and budget policy | Owner-controlled |
| `MessageAttachment` | Durable Telegram metadata and processing state | No unbounded media-content retention |

All migrations are additive until the legacy path is explicitly retired. Backfill creates
links and canonical records without publishing, notifying or removing existing locks.

## 9. Security, privacy and retention

- Group isolation is applied to every query and contract.
- Telegram content, attachments and memory are minimized before a DeepSeek call.
- Author identity uses a stable group-local pseudonym unless a product rule requires more.
- Provider keys, authorization headers, signed URLs and raw chat excerpts are excluded from
  ordinary logs and owner telemetry.
- Raw provider response retention is enabled only for development/shadow diagnostics behind
  a feature flag, with technical-admin access and a 30-day TTL. It is disabled for the
  normal production writer after the diagnostic phase unless separately re-approved.
- `RawMessage.delete_at`, `bot_added_at`, import consent and source availability remain
  mandatory constraints.
- Derived memory cannot preserve deleted personal conversation indefinitely. Structured
  academic facts may remain only under the approved card/evidence retention rule.
- Prompt injection text is handled only as quoted source content. Backend rules control
  URLs, source IDs, dates, writes, locks and notifications.

## 10. Cost and performance

The initial batching values of 10–30 messages and 2–5 minutes are experiment parameters,
not product constants. Cost is measured without assuming a subscription price:

- input/output/cache tokens per 100 raw messages;
- cost per unique accepted academic item;
- weekly/monthly cost per group;
- repeat-context token ratio;
- retry and rejected-output cost;
- normal and urgent path latency;
- shadow overhead.

The engine sends delta messages, selected active segments and compact trusted memory rather
than replaying a chat. Context Understanding may group multiple deltas in one request;
Academic Extraction runs only for new or changed relevant segment versions. Caching is used
only when the API capability and measured benefit are verified.

## 11. Compatibility and rollout

1. Default mode is `legacy`.
2. `shadow` receives the same permitted source snapshot but cannot call the publication or
   notification adapters.
3. Shadow artifacts, costs and errors are isolated from legacy checkpoints and user state.
4. Gold matching compares canonical entities, fields, display quality, merges/splits and
   update actions, not proposal counts.
5. Owner-approved gates permit `new` for one pilot group.
6. At any point the owner may switch `new -> legacy`; new memory remains read-only and no
   historical notification is replayed.
7. Legacy schema and API removal is a later, separately approved program.

## 12. Alternatives considered

| Option | Benefit | Limitation | Decision |
|---|---|---|---|
| B: replace prompts/provider only | Small change | Keeps target boundary, no memory or canonical updates | Reject as final architecture |
| C-lite: typed contracts and better target context | Lower migration risk | Still weak for late clarification and entity identity | Useful intermediate state through PR-2 |
| C+: preserve infrastructure, replace semantic core incrementally | Solves context, memory, evidence and updates while retaining guards | More migrations and staged rollout | Recommended |
| D: rewrite the entire module/backend | Maximum design freedom | Reintroduces solved security, concurrency and compatibility risks | Reject |

## 13. Technical decisions fixed by this proposal

These decisions do not change an approved product rule:

1. Use C+ and additive migration.
2. Separate technical, academic and application outcomes.
3. Keep accepted and rejected proposals together in validation results.
4. Use discussion segments and versioned incremental batches.
5. Keep structured memory with evidence; no free-text summary as source of truth.
6. Separate structured academic facts from generated display text.
7. Introduce canonical items behind compatibility projections.
8. Run the new engine in shadow before any product writes.
9. Maintain a single writer per group.
10. Do not fix or infer a subscription price in this program.

## 14. Approved product decisions

The product owner approved the following decisions on 2026-10-12:

1. A missing homework deadline is derived from the next reliable lesson of the same subject
   as `schedule_inferred`. Students see a neutral “к следующему занятию” label; the
   unconfirmed-source marker is visible only to the headman.
2. The product exposes two card destinations—tasks and deadlines. Event words such as test,
   credit, exam and control point are semantic content used in titles and descriptions.
3. AI is given broad freedom to interpret connected context and publication is automatic by
   default. Only serious conflicts, material ambiguity, explicit model doubt and human locks
   block it. Minor uncertainty creates a headman-only attention marker.
4. Validation keeps every valid sibling. Exactly one repair call is permitted only when the
   provider response is technically unparseable or truncated, never because content is
   incomplete or unusual. The trigger and spend are recorded separately.
5. During development and shadow, the full provider response is retained behind a restricted
   feature flag for 30 days, then deleted automatically. The feature is disabled after the
   diagnostic phase unless re-approved; structural outcomes remain.
6. Partial but actionable cards are visible to students. Missing optional fields do not
   block publication; unusable or materially misleading proposals go to review.
7. Explicit subgroup assignments become separate, labelled cards that are currently visible
   to the whole group. Membership-based filtering is a documented future product block.
   Explicitly personal content is never converted into a group-wide assignment.
8. AI-generated semantic titles and descriptions are the primary path. A deterministic,
   evidence-only fallback is allowed solely when the generated display text is absent,
   malformed or fails factual validation; it carries a headman-only attention marker.
9. Explicit cancellations, reschedules, corrections to published dates and near deadlines
   may receive queue priority. The numeric latency target and protected budget are set only
   after shadow measurements.
10. A minimal read-only production diagnostic for the two disputed historical messages is
    authorized. It may read status, attempts, rejection reasons and projection links only;
    it cannot lock, replay, mutate or block card creation.

Gate G0 is approved. Implementation, local/disposable-database migrations and tests may
proceed through the roadmap. Paid provider experiments, production migrations and writer
rollout remain separately gated by their explicit stages.
