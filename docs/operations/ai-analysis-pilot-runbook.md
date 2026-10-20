# AI analysis shadow and pilot runbook

**Current status:** the C+ worker path is `shadow-ready`: dispatch, durable budget reservation,
retry recovery and product-write isolation have passed the complete PostgreSQL suite. A production
deployment and a paid replay still require an explicit owner action. The `new` writer remains
disabled and is not authorized by this document.

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

1. Apply migrations through `0033` to a disposable PostgreSQL database and rehearse
   upgrade/downgrade. SQLite is not evidence for lock or concurrency behaviour.
2. Run the complete backend suite with `POSTGRES_TEST_URL` so the PostgreSQL-only checks run.
3. Validate and adjudicate the private held-out corpus.
4. Obtain approval for paid provider calls, the per-group shadow budget and sampling rate.
5. Confirm that production secrets and Telegram content do not appear in logs or reports.

## Controlled transitions

All changes require the group UUID, named actor and reason and are appended to
`group_analysis_mode_history`. Run from `apps/backend` with the production environment loaded.
For the single owner-controlled pilot group, the private Telegram command below prepares a
historical replay from the persisted `bot_added_at` boundary. It requires a confirmation button,
does not delete legacy results and does not itself enable provider calls:

```text
/shadowrun 0.25 100
```

The arguments are the hard daily shadow budget in USD (`0.04`–`1.00`) and the deterministic
sample percentage (`1`–`100`). Use `/shadowstatus` for aggregate queue state, tokens and charged
cost; it never prints source message text. For a replay that must not run the legacy writer, keep
`AI_ENABLED=false` and use `AI_SHADOW_ENABLED=true` with an absolute
`AI_SHADOW_ENABLED_UNTIL`. Ordinary live shadow may still use the global AI switch; both paths
remain subject to the per-group shadow budget.

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

## Hybrid adapter canary and backfill from a chosen date (ADR-010)

Pipeline: RawMessage -> context -> Pass 1 -> Pass 2 (`cplus-extraction-2`) -> Python adapter ->
strict validation -> optional single AI Repair -> shadow row. Shadow rows are the only product of
these steps; students see cards only after `promote --apply`.

The old per-message engine is hard-locked: it publishes cards only when `LEGACY_ENGINE_ENABLED=true`
(default false); `AI_ENABLED` alone no longer wakes it up. Leave it false while a group is on the new engine.

Settings (all default off): `AI_ENABLED=false`, `AI_SHADOW_ENABLED=true` with an absolute
`AI_SHADOW_ENABLED_UNTIL`, `AI_SHADOW_CANARY_ONLY=true` for the first run, and
`AI_OUTPUT_REPAIR_ENABLED=false` until paid repair is separately approved. A single package
(both passes plus repair) is capped at USD 0.04 (`SHADOW_PACKAGE_CAP_USD`); repair is skipped,
not truncated, when the remaining cap cannot cover its conservative worst case.

Order of work, each step reversible and each paid step needing owner approval:

1. `plan` (read-only) reports eligible stored messages, run states, measured cost per run and an
   estimate with a 1.5x margin. Only messages stored since `bot_added_at` (and not expired) can
   be processed; Telegram history is never fetched.
2. Canary: the existing one-package canary (`AI_SHADOW_PREPARE_ONCE_KEY`), cap USD 0.04.
3. `requeue --apply` re-runs runs lost to the old defect (failed, or completed with only
   rejections) from their stored input snapshot; `enqueue --apply --limit N` adds missing runs
   oldest first. Both are idempotent; the worker's daily shadow budget limits spend and speed.
4. `review` prints accepted cards with their source messages for the owner (private text, never
   commit or paste it).
5. `promote` (dry-run) then `promote --apply --limit N` writes through the canonical writer.
   Manual locks and audience rules apply, expired items are skipped, no notifications are sent,
   and every run is marked promoted exactly once.

```bash
python -m studgroup.analysis_backfill plan --group-id GROUP_UUID --since 2026-10-08
python -m studgroup.analysis_backfill promote --group-id GROUP_UUID --since 2026-10-08 --limit 10
```

Wave preflight (read-only, `AI_SHADOW_WAVE_PREFLIGHT=true`, logged as `SHADOW_WAVE_PREFLIGHT`):
the unit of a wave is one stored source revision = one package = one paid run. Context overlap is
not merged, so neighbouring messages are paid again by each package. Revisions that already have a
run (any state, including `failed`) are excluded from `enqueue`, and the canary-only worker claims
only queued rows carrying a preparation key, so failed runs are never retried unless `requeue` is
run separately. The report holds ids, dates, counts and money only: window size, uncovered package
count and composition, the next wave (oldest first, no keyword filter), per-day volume, measured
cost with the 1.5x margin, the hard cap (USD 0.04 per package), the route budget, and, once a wave
is prepared with `AI_SHADOW_BACKFILL_KEY`/`AI_SHADOW_BACKFILL_LIMIT`, the summed request envelopes
and blocking codes (AI flags, repair, sample percent below 100, leases, foreign prepared rows,
claimable failed rows, package cap, route budget).

Spread selection (`AI_SHADOW_BACKFILL_SPREAD=true`, off by default; chronological `enqueue` is
unchanged): `AI_SHADOW_BACKFILL_LIMIT` unprocessed revisions are picked across the whole window.
Slots go to days in proportion to volume (every day gets one when the limit allows), rotate over
topics inside a day, and each pick is the message farthest in time from those already chosen that
day. There is no keyword or task-word filter, so ordinary chatter is sampled too. The choice is
deterministic and idempotent: revisions with any run, failed ones included, are never picked, and
the wave key blocks a second wave. The preflight shows days, topics and message ids of the
selection, and for a prepared wave the exact context overlap between packages.

Wave results (read-only, `AI_SHADOW_WAVE_RESULTS=true`, logged as `SHADOW_WAVE_RESULTS`): run
states and error codes, tokens and charged cost, proposals found/accepted/rejected by kind, action
and reason, and the same cards `/shadowcards` previews (status, kind, subject and deadline coverage,
multi-source cards, confirmed materials, source messages cited by several cards as a duplicate
signal). No message text is logged. A failed run is never claimed again by the canary-only worker;
only an expired running lease is recoverable, and the preflight reports it.

Known limit: cancellation, reschedule and amendment of an earlier item need its identity. Backfill
runs cannot supply one before promotion, so those proposals are rejected as
`mutation_identity_missing`; review such source messages manually.

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

### Owner-chosen canary message and card review without a shell

- `AI_SHADOW_PREPARE_MESSAGE_ID=<telegram message id>` (used only together with `AI_SHADOW_PREPARE_ONCE_KEY`, while `AI_ENABLED` and `AI_SHADOW_ENABLED` are false) prepares exactly that stored message instead of the oldest eligible one. It bypasses only the "newer than the shadow switch" window; the `bot_added_at` bound, retention, one-run-per-revision and the single marked package still apply. An unknown id prepares nothing (`preparation_empty`).
- Canary sequence: set key + message id + `AI_OUTPUT_REPAIR_ENABLED=false` → deploy → check `AI_STARTUP_DIAGNOSTICS` shows one prepared canary and `/shadowstatus` → owner approves → `AI_SHADOW_ENABLED=true` with an absolute `AI_SHADOW_ENABLED_UNTIL` a few minutes ahead, `AI_SHADOW_CANARY_ONLY=true` → check `/shadowstatus` "Принято: N" → switch shadow off and clear key and message id.
- `/shadowcards` (owner's private chat only, same gate as `/shadowstatus`) sends up to 5 accepted cards with cited source messages and `t.me/c/...` links to the owner. It writes nothing and publishes nothing to students.
- `/shadowexport` resolves the stored `wave-a-spread-1` to its own group, asks the configured
  owner for a 15-minute single-use confirmation, and sends a bounded in-memory `.json.gz` through
  the private Telegram chat. It does not call AI or store the export in the outbox or filesystem.
  Telegram retains the sent document in the owner's cloud chat; download it and delete the
  message when that copy is no longer required. A failed or ambiguous upload is not retried
  automatically; issue the command and confirm again.

### Quality rules added after the first real canary (prompt `cplus-extraction-3`)

- Audience: "каждый / все / всем / вся группа" addressed to the whole group is `group` even when each student works alone. The prompt says so, and the adapter converts a model `personal` to `group` only when the cited text is collective AND contains no personal address (a name followed by `,`/`:`, "Старосте", "тебе", "ты", `@user`, "лично"). Genuine personal instructions stay `personal` and held.
- Deadline origin: `basis=explicit` must be backed by a written date, a resolvable relative word ("завтра", "послезавтра") or a named weekday within two weeks. Anything else ("к следующей паре" needs a trusted timetable that shadow input does not carry) is cleared to `unknown` with the attention reason `relative_deadline_unresolved` (or `deadline_not_in_source`) and shown for clarification. The model's computed date is never promoted to a stated date.

### Bounded shadow backfill wave without a shell

- `AI_SHADOW_BACKFILL_KEY=<a-z0-9-> AI_SHADOW_BACKFILL_LIMIT=<1..200>` (with `AI_ENABLED=false`, `AI_SHADOW_ENABLED=false`) queues one marked wave of the oldest stored messages without a run since `bot_added_at` at startup (`SHADOW_BACKFILL_WAVE wave_prepared_N`). The wave key is carried on the rows, so a restart with the same key never queues a second wave (`wave_exists`).
- Only a canary-only worker (`AI_SHADOW_CANARY_ONLY=true`, `AI_SHADOW_ENABLED=true` until an absolute `AI_SHADOW_ENABLED_UNTIL`) claims marked rows, so the wave size is the hard bound on paid packages; the group's shadow daily budget and the global/hourly limits still apply.
- Review with `/shadowcards [offset]` (5 per page, owner only). Nothing is published; promotion is a separate, explicitly approved step.

### Card quality, context, materials and owner preview (prompt `cplus-extraction-4`)

No paid call, no migration, no production writer change.

- **Text hygiene** (`analysis/text_hygiene.py`, applied inside `card_editor`, i.e. the same path
  the future writer uses): service hashtags (`#орг`, `#матан`), @mentions, greetings and stray
  spacing are removed from AI title/description; digits, pages, links and requirements are never
  touched. A generic or tag-only AI title is replaced by the cleaned first clause of the source
  (or `«<вид>: содержание в сообщении чата»` if there is no text) — never `Учебное задание…`.
  `#орг` and similar service tags are never a subject.
- **Context**: files and photos without a caption are now evidence for neighbouring-message
  linking (direct replies, same media group, ordinary neighbourhood); `SourceMessage` carries
  `author_role` (student/deputy/headman) and `SourceAttachment.file_name`. Both are optional, so
  frozen snapshots stay valid. Adjacency alone is not proof; the prompt tells the model to link on
  reply/topic/enumeration/explicit pointers and to give the headman priority.
- **Changes**: shadow has no trusted `existing_items`. A `reschedule`/`update` without identity
  is kept as a *held* proposal with reason `unconfirmed_change` (never a silent edit). A `cancel`
  without identity is rejected and listed by `/shadowcards` as "отклонено валидацией".
- **Materials** (`analysis/card_materials.py`): derived only from stored metadata — attachments
  and URLs of cited messages, direct replies to them and members of the same media group. Other
  nearby attachments are shown as *unconfirmed candidates*. File contents are never read
  (OCR/PDF/multimodal is a separate stage).
- **Preview**: `/shadowcards [offset]` runs the real `edit_card` → resolver → projection for all
  completed accepted shadow runs inside one savepoint that is always rolled back, then renders
  title, clean description, subject/type, deadline + reliability, audience, materials,
  history of supplements, source links and review reasons. Owner-only, private chat, never an
  HTTP API; DB unchanged (verified by `tests/test_postgres_preview.py`).

Mini App contract proposal for Codex (not implemented): card `materials: [{group: photo|file|media|link,
kind, title, url?, telegram_link, message_id, via: cited|reply|album}]`, derived at read time
from `CanonicalItemEvidence` → `RawMessage` → `MessageAttachment` + URLs in text; no storage
change. Telegram file ids stay server-side; the client only gets `t.me` links.

### Final stabilisation: titles and reliable supplements

- **Title order** (`card_editor` + `analysis/title_builder.py`, no extra AI call): the model's own
  title when sound -> action clause of the model's *evidenced* description -> action clause of
  the cited source text (greetings, fillers, modal wrapper, delivery condition, deadline prefix
  and `#tags` removed; "сделать презентацию" -> "Подготовить презентацию") -> neutral
  `«<вид>: уточнить по сообщению в чате»` with reason `title_insufficient_data`. Titles that
  start with a greeting/filler are rebuilt (`noisy_title`); good titles are not touched. The
  prompt's title contract is: action + object + topic, only what the messages confirm.
- **Supplements** (`analysis/linking.py`): before the model runs, every snapshot message gets
  structural link candidates (both earlier and later messages): strong = direct reply or same
  media group; medium = reply-to-reply, enumeration/continuation by the same author in the same
  topic; weak = adjacent file. They are sent to the model as `link_candidates` (ids and facts
  about the link only, no text). After extraction `reconcile_validation` (idempotent) adds
  provable strong members: files/albums as sources, a reply text only from the headman/deputy or
  the task's author, only when it carries content and is not itself a new task, appended
  verbatim as `Дополнение: …`. A separate model card that only clarifies another card is merged
  (`merged_supplement`). Ambiguous (one reply, several tasks) -> `supplement_ambiguous`;
  doubtful medium/student replies -> `possible_missing_supplement`; a cited message with no
  structural tie to the others -> `foreign_source_attached` (flagged for the headman; published, no longer held - owner decision 20 Oct 2026). Cross-run: in preview/promote
  `with_supplement_updates` turns a card that merely clarifies an already-built item into an
  update of that item. Old stored shadow results get the same treatment when reviewed.

### Card text quality: independent fields (prompt `cplus-extraction-5`)

No paid call, no migration. Supersedes the title order and the verbatim `Дополнение: …` rule of
the section above.

- **Prompt**: the title is 2-8 words (ideally under 60 characters), names the work and never
  starts with an address, question or remark; the description is one to four sentences in the
  model's own words (what, for whom when only part of the group is affected, format, materials),
  merged from every cited message, never quoted or pasted chat text.
- **`card_editor.edit_card` judges title, description and subject separately.** A subject problem
  (`unsupported_subject`) drops only the subject. A bad title is rebuilt without touching a sound
  description and vice versa. Held/attention visibility rules and `SERIOUS_REASONS` are unchanged.
- **Fact check** (`facts_problem`): numbers (list markers `1)` ignored), calendar words,
  subgroup wording and named works must come from the cited messages. A work/concept counts as
  supported by any word of the same family (`выступление` for `доклад`, `КТ3` for
  `контрольная точка`), so a faithful paraphrase is kept. Invented facts still fail.
- **Titles**: conversational phrasing (`У кого…`, `Если кто-то…`, `Это…`, `Не…`, questions,
  truncated `…`) never survives. A rebuilt title must be a safe action or named work taken from
  the evidenced description, then from the source; otherwise `«<вид>: уточнить по сообщению в
  чате»` with `title_insufficient_data`. An overlong but sound title is shortened from the
  description (`title_too_long`).
- **Descriptions**: an unsupported description keeps its supported sentences (`description_trimmed`);
  if none remain it becomes the neutral marker `Описание уточняется: подробности в исходном
  сообщении чата.` (`description_pending_ai`) until the AI re-reads the item. Source messages are
  never copied into a description; they stay in the card's sources.
- **Supplements** (`linking.reconcile_validation`, `backfill.with_supplement_updates`): a
  provable clarification (reply from staff/the same author, strong link) is added as a cited
  source marked with the fact path `supplement_reply`; its raw chat text is never appended. Only a
  clarification that says something the card lacks (`card_editor.supplement_adds_information`: a
  new number, calendar word or named work, or at least two new content words) adds
  `supplement_pending_integration`; a reply that merely repeats the card adds the source and
  nothing else. The check errs towards "new" (a missed integration is worse than a flag). The
  resolver records a new source even when no visible field changed.
- **A lagging description is never presented as final.** A student does not see the
  `needs_clarification` state, so `projection.visible_description` appends one neutral sentence
  (`LAGGING_NOTE`, points to the source messages, states no fact) to the projected description
  while `supplement_pending_integration` or `description_pending_ai` is among the reasons. The
  canonical description stays clean and the note disappears when the AI integrates the
  clarification (the reasons are replaced). The placeholder description carries no note.
- **A failed field never destroys an accepted text** (`resolver._kept_texts`): when an update's
  description (`description_pending_ai`) or title (`title_insufficient_data`) had to fall back to a
  placeholder, the item keeps the text it already has; the reasons keep the card marked
  unconfirmed. A previous placeholder is replaced by the first sound text. Why `resolver.py`
  changed in a text-quality task: without this the placeholder written by the editor would
  overwrite a previously accepted description on every update, i.e. the new fallback would be
  *worse* than the old behaviour. The other resolver change records new evidence on `no_change`.
- **Visibility**: only `SERIOUS_REASONS` hold a card. A card with a placeholder or trimmed text
  is published as `needs_clarification`, never as a confirmed card. A long but otherwise sound
  title is kept (cut by title hygiene) and raises no flag.

### Discovery vs category: held candidates (prompt `cplus-extraction-7`)

No paid call, no migration, no new API call per message (still context + extraction; the optional
repair call is unchanged). Student API, `semantic_kind`, `destination` and the PostgreSQL models are
untouched.

- **Two logical steps in one extraction answer.** Step 1 discovers anything possibly useful to
  students (study work, checks, events, organisational instructions, news) without keyword
  triggers; step 2 lets the model choose the product category from the meaning in context:
  ДЗ (`homework`, task), КТ/проверочные (`test`, `control_point`, `assessment`, `credit`, `exam`,
  deadline), events and everything else as `candidates`. Ordinary conversation produces nothing.
- **Contract extension (backward compatible).** `AnalysisOutput.candidates` is optional
  (`CandidateProposal`: `category` event|organizational|information|other|homework|check, text
  fields, `reason`, `when_text`, evidence). Stored in the existing `validation_result_json` as
  `ValidationResult.candidates` (`UnpublishedCandidate`, adds `hold_code`, `origin`, `model_label`,
  `proposal_ordinal`, `issues`). Results stored earlier simply have none.
- **Salvage instead of loss.** A proposal the strict contract rejects (unknown kind such as
  `event`/`quiz`, missing kind, unknown field, unusable evidence) keeps its readable content as a
  candidate with `origin=rejected_proposal`, `hold_code=rejected_<issue>`; `RejectedItem.salvaged`
  says so. Evidence is only ever a message of the request (defaulting to the target message,
  flagged `evidence_defaulted_to_target`). Non-objects and content-free items leave a counter only
  (`rejected_without_content`). A candidate that duplicates a card is dropped
  (`candidate_duplicates_card_discarded`); an optional repair that recovers a proposal removes its
  held copy.
- **Never published.** `apply_validated_batch`, the resolver and projection read only `accepted`;
  candidates appear solely in the owner preview (`held_candidates`, footer «Не карточки…») and
  `wave_results` (`held_candidates` by category and hold code). Events stay unpublished until a
  frontend/API category exists; the compatible way to add it later is a new product category in the
  projection layer reading these stored candidates, with no change to the seven `semantic_kind`s.
- **Not changed on purpose.** The legacy `ACADEMIC_CUE` gate applies only when
  `ai_live_two_pass` is off (the two-pass C+ path sends every message to the model). Pass 1
  (`cplus-context-1`) is untouched. The strict adapter-free reference validator ignores
  `candidates`.

**04B (prompt `cplus-extraction-7`).**

- *Synonyms.* Before strict validation the adapter maps whole-label synonyms to the published
  kind: quiz/квиз/тест → `test`; самостоятельная, контрольная → `assessment`; КТ → `control_point`;
  зачёт → `credit`; экзамен → `exam`; ДЗ → `homework` (counter `semantic_kind_synonym_normalized`).
  Unknown or ambiguous labels (проверочная, опрос, lecture, event) are not guessed and are held.
  Source, audience and destination checks still apply after normalisation.
- *Source honesty.* `UnpublishedCandidate.source_status` is `verified`, `partially_verified` or
  `defaulted_to_target`; `claimed_source_ids` keeps what the model cited. A candidate with no
  confirmed source gets `hold_code=unverified_source` and is shown in the preview as
  «источник не подтверждён моделью»; `wave_results` counts `by_source_status`.
- *Events.* `candidates.event_records` yields `EventRecord` (title, description, `when_text`,
  reason, audience, sources, fingerprint, `promotion_blockers`, `published=false`). Contract for
  Codex: `docs/architecture/event-records-contract.md`. Batch plan:
  `docs/operations/ai-batch-processing-plan.md` (analysis only, nothing implemented).

### Subject catalog (prompt `cplus-extraction-8`)

The group's real subjects (`AnalysisInput.subject_catalog`, built by
`analysis/subject_catalog.load_subject_catalog` from the schedule and verified memory facts) go to
the extraction call; the model picks the subject by meaning and answers `subject_id` + canonical
`subject` (or null). The adapter only verifies it: confirmed by id or exact name/alias, unknown id,
ambiguous label and (for a complete catalog) an off-catalog subject are never confirmed; a foreign
or empty catalog is ignored. `resolver` stores `edited_card.subject` and never erases an existing
subject. Counters: `subject_confirmed_by_catalog`, `subject_canonicalised`, `subject_id_unknown`,
`subject_not_in_catalog`, `subject_ambiguous_in_catalog`, `subject_catalog_ignored_*`. Contract and
what Codex still has to plug in: `docs/architecture/subject-catalog-contract.md`.
05A: subject ids are `uuid5(group_id, name.casefold())` (`studgroup/subject_identity.py`, the one
formula of every writer and of the student API; similar names are never merged); the catalog is not part of run identity or the source hash, its
`fingerprint` is recorded in `metrics.subject_catalog` (and per wave in `wave_results`), and an
owner requeue refreshes the stored catalog. New reasons: `subject_id_mismatch`.

### Stored results are re-normalised on read (`analysis/stored.py`)

A stored shadow `validation_result_json` keeps the proposals exactly as the code of that day
produced them, so later deterministic safeguards never reached them (the 4575 date computed
from "к следующей паре" and the 4597 `personal` audience were such cases). `effective_validation`
re-applies, on read, the same source-checked rules the adapter uses (`_Normalizer.semantic_rules`:
date origin, collective address -> group, service-tag subject) plus the supplement reconciliation.
It is used by `backfill.review`, `backfill.promote`, `/shadowcards` preview and, as defence in
depth, by `apply_validated_batch` itself, so no path (preview, promote, live writer, Mini App
projection) can read the old values. Idempotent; sources are never replaced; literal-date
conflicts stay serious; no AI call.


## New (package) engine for groups in mode `new`

The worker lives in `analysis/package_worker.py` and is reached through `dispatch_next`. It serves
only groups whose `analysis_mode` is `new`; the legacy engine never sees them and the worker never
touches `legacy`/`shadow` groups (one writer per group).

- Flags (all default off): `NEW_ENGINE_ENABLED`, optional absolute `NEW_ENGINE_ENABLED_UNTIL`,
  `NEW_ENGINE_NOTIFICATIONS` (student pushes for new cards). The group's `shadow_daily_budget_usd`
  is the daily spend cap; `AI_LOOP_PAUSE_SECONDS` and the hourly schedule apply as before.
- Per iteration: one package (up to 24 target messages with read-only context) -> one durable
  `shadow_evaluation_runs` row without a raw message -> two provider passes under a reservation ->
  in the same transaction the accepted proposals go through the canonical resolver with the
  timetable subjects and the intelligent-deadline context, and the targets become `completed`.
  A lost lease re-runs the package; the resolver is idempotent. Failures retry (3 attempts) and then
  park the targets as `failed`.
- The model receives the group's recent active cards (`existing_items`: id + revision), so a later
  mention updates the same card instead of creating another.
- Owner re-analysis of a window: `python -m studgroup.analysis_backfill reopen --group-id UUID
  --since 2026-10-08` (dry-run; `--apply` sets the stored live messages `pending` again with a new
  generation). It changes no card; clean up legacy-written cards separately and deliberately.
- Switching a group: `shadow -> new` needs the explicit gate approval (`transition_mode`); rollback
  is `emergency_rollback` back to `legacy`, which keeps all product rows.
