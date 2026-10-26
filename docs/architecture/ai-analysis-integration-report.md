# AI analysis integration report

Date: 2026-10-26. Scope: stage 0 of the Core v2 image-quality brief; evolutionary C+ changes only.
Source: `origin/main` at `dbc59638bcde89daec29e2d71c6c51bb95c29d54`.
Working branch: `codex/core-image-audit`, in a separate managed worktree.

## Baseline and scope

The original checkout is `codex/ai-analysis-core`, one commit ahead and 82 behind current
`origin/main`, with unrelated uncommitted files. Local `main` is 20 commits behind
`origin/main`. Fetch succeeded. The audit uses current remote main without merging, resetting
or changing the original checkout. No production configuration, provider call, published card,
frontend file or shared API contract was changed. Obsolete documentation was not used.

This report replaces the old integration snapshot in place. The implementation plan is in
[the current roadmap](ai-analysis-rebuild-roadmap.md#18-core-v2-image-quality-work).
The system already has a live package writer, durable shadow execution and canonical projection;
the earlier integration report's missing-writer blockers no longer describe this source revision.

## Actual source-to-student path

1. `ingestion.py:attachment_metadata`, `store_message` and `sync_attachments` persist media-only
   messages, the largest Telegram photo variant, document metadata, album IDs and attachment
   revision history. The multiple Telegram photo sizes are variants of one image, not pages.
2. `analysis/dispatch.py` selects shadow, package/new or explicitly enabled legacy execution.
3. `package_worker.py:_plan_next` gathers targets and bounded surrounding messages. Before
   creating a run, `_read_pending_photos` downloads and reads up to six photo attachments.
4. `photo_reading.py:read_photo` sends pixels and at most 500 caption characters to ONE vision
   call, requests at most 900 output tokens and parses relevance/summary/text. There is no
   second visual call and no chat context, existing cards or timetable in this visual request.
5. `_source_messages` uses `photo_dedupe.distinct`, including 14 days of older anchors, to change
   duplicate input attachments to `irrelevant`. `enriched_text` then excludes their OCR.
   The stored attachment state remains intact; loss occurs in the semantic input snapshot.
6. `run_shadow` performs Context Understanding and Academic Extraction, both text-only.
   Adapter, strict validation, evidence checks, canonical resolver/writer, deadline policy,
   manual locks and compatibility projections remain the existing C+ foundation.
7. `student_cards.py:projected_materials` independently loads active attachments from cited
   messages and runs `distinct` again. A ready photo with caption <=120 characters has
   `replaces_description=true`. The existing public contract explicitly tells the client to
   replace the text description with this photo.

## Confirmed defects and limits

| Finding | Source / deterministic evidence | Consequence |
|---|---|---|
| Deduplication before semantic merge | `package_worker._source_messages`; three-crop baseline | All 3 pictures are read, but only 1 reading reaches extraction; unique facts disappear. |
| Text similarity mistaken for visual equality | `photo_dedupe.words/same_content`; four-slide baseline | Single-digit numbers are discarded; 4 distinct exercises collapse to 1. Older anchors can suppress a better new crop regardless of its extra content. No pixels enter deduplication. |
| Incomplete packages marked completed | `_read_pending_photos` slices to 6; 1/6/7/12 baseline | 7 pictures leave 1 unread, 12 leave 6 unread, while every source is completed. Next sweep is idle without configured backfill. |
| Backfill is conditional, not guaranteed continuation | `reopen_unread_photo_messages`, `photo_backfill_since` | Configured backfill can reopen eligible non-imported messages repeatedly until no metadata-only photo remains. It is not correct to claim every seventh photo is always lost permanently. |
| Image documents not read | pending query requires `kind=photo`; PNG-document baseline | Document is retained but receives zero vision attempts. |
| Truncation without explicit incomplete status | `parse_reading`, `enriched_text`, `SourceMessage` | OCR silently sliced at 2500 characters, summary at 300, enriched message at 12000. A valid JSON response is not checked for `finish_reason=length`. |
| Insufficient visual semantics | `PHOTO_PROMPT` categories | No schedule/in-class categories, mixed types or structured per-image facts. Academic checks are merged into image category event. |
| Photo facts discouraged downstream | `extraction_strategy._EXTRACTION_INSTRUCTIONS` | Prompt tells extraction to keep photo descriptions general and avoid exact numbers. This directly conflicts with complete descriptions in the new brief. |
| Errors treated as completed package input | download/MIME/non-retryable failure paths; existing tests | Unavailable Telegram downloads are final `failed` on first attempt. No durable error code, per-image retry counter or distinction between temporary and terminal download errors. A package may continue to proposals without readable photo evidence. |
| Retryable vision has no image attempt limit/backoff | `_read_pending_photos` returns `photo_retry` before creating run | Text-run attempt limits do not cap these repeated image calls. Successful earlier readings are retained, but their charge is lost on this retry path. |
| Vision before budget reservation | `_plan_next` runs before `_claim`; zero-budget baseline | Even a group with zero budget receives a vision call, then its text run is deferred. No per-image cap/reservation. |
| Costs only partially observable | local `photo_cost` added to run at enqueue | Vision tokens/latency/version are not stored per image; successful earlier work costs disappear on `photo_retry`. A crash before commit can repeat a paid call. |
| Context and version limitations | `_source_messages`, `SourceAttachment`, attachment model | OCR is merged into message text without attachment IDs or fact-level image evidence. Caption-only edits leave old photo reading active; any future contextual pass must invalidate by context revision too. |
| Different presentation paths | `card_materials._from_message` vs `student_cards.projected_materials` | Preview derives materials from citations/replies/albums with `contents_read=false`; API derives from canonical cited sources and independently suppresses photos. No shared per-image selection result. |

The package group-settings row lock serializes planning in PostgreSQL. Existing concurrency tests
prove package-level idempotency, not crash-safe paid image analysis: no independently committed
per-pass lease/checkpoint exists. Preserve these locks while adding image jobs.
The source attachment query and anchor query lack explicit attachment expiry/source-revision
predicates; future versioned image results must check group, active source and context revisions,
retention and lease ownership at both claim and persistence.

## Shadow, preview, events and schedule

- Shadow source preparation (`processing` / `academic_context`, `shadow_worker.enqueue_shadow`)
  supplies attachment metadata and original text, without the live photo-reading enrichment.
  Owner preview replays stored results under a rolled-back savepoint; it does not run vision.
  Comparing current shadow and live results is therefore not a same-image-input comparison.
- `candidates.event_records` already provides unpublished `EventRecord` DTOs with verified
  sources/audience blockers. Storage is the run JSON with 30-day retention. It is not a durable
  public event lifecycle or student endpoint. Existing graded-event/deadline endpoints are a
  different product entity; do not reuse them to disguise a poster as homework or a test.
- See [event contract](event-records-contract.md). It explicitly labels student publication as a
  proposal requiring product decisions. Dates remain `when_text`; no full location/lifecycle yet.
- Timetable writes and audit already exist in `headman_schedule.py` / `schedule_audit.py`.
  C+ has no structured image-to-schedule suggestion adapter or confirmation lifecycle.
  Official timetable writes must stay in the authorized confirmation flow.
- Text classification already distinguishes homework, checks, events, organisational information
  and candidates. Its full replacement is unnecessary. Correct model handling of in-class work,
  ambiguity and mixed image entities has NOT been measured by returning predetermined fake JSON.

## Offline evidence and quality baseline

`tests/test_image_analysis_baseline.py` adds 10 observational checks. They pass by reproducing
current defects and are explicitly NOT new-behaviour acceptance tests. Convert each observation
to the intended invariant as the responsible implementation stage lands; never weaken an existing
acceptance test to make a fix pass.

`tests/fixtures/ai_analysis/image-scenarios.json` records 11 entirely synthetic scenarios and
expected facts/actions: crops, pages, classwork, homework, ambiguity, timetable, poster, personal
invitation, mixed entities, correction, unreadable input. These are text annotations for future
image fixtures, not actual labelled pixel/model outputs. No private chat data was copied.

Measured stub baseline: image attempt coverage is 1/1, 6/6, 6/7 and 6/12 on the first completed
package with backfill unset. Three-crop semantic input retains 1/3 synthetic readings. Four
similar-format pages retain 1/4 attachments. Those ratios describe these deterministic cases only;
they are NOT population recall, precision or actual vision quality estimates.

Using the repository's accounting formula, a fake successful vision answer with 900 input and
60 output tokens costs USD 0.000342. Two text stubs together cost USD 0.000018. The retry probe
performs two successful readings but records only USD 0.000360 instead of USD 0.000702. This is
an accounting regression, not verification of current provider prices. Actual expenditure: USD 0;
real latency and quality, comparative precision/recall and provider cost remain unmeasured.

## Checks and stage verdict

- Initial focused suite: **153 passed**, 0 failed; 23.10 s.
- Initial full suite without PostgreSQL URL: **1078 passed, 26 skipped**, 0 failed; 100.38 s.
- New baseline checks: **10 passed**, 0 failed; 2.64 s.
- Focused real PostgreSQL worker/pipeline/student API: **11 passed**, 0 failed; 6.99 s.
- Full suite including PostgreSQL and new checks: **1114 passed, 0 failed, 0 skipped**; 112.69 s.
  Command: `POSTGRES_TEST_URL=<disposable-local-db> uv run pytest -q`.
- `uv run ruff check .` and `git diff --check`: passed after all additions.
- Disposable PostgreSQL 17, localhost-only port 55439: migration upgrade `base -> 0039`,
  downgrade `0039 -> base`, second upgrade `base -> 0039` all succeeded. No schema edits.

Stage verdict: **PASS for the offline technical audit**. Transition to stage 1 awaits owner
agreement required by the brief. Product image quality is not accepted by this audit; model
classification and pixel-reading accuracy are unmeasured. No implementation fix has shipped.
No paid model calls were made; no deployment or writer-mode switch was performed.

## Owner decisions and next gate

Before leaving stage 0, agree the staged plan below and resolve the implementation policy for
unsupported/terminally unreadable or budget-deferred images. Recommendation: retain an explicit
image status; defer unfinished reading, and hold publication that depends on unreadable facts for
review, while allowing independently supported text-only facts under existing publication rules.
This changes when a mixed text/photo message can publish and needs an explicit owner decision.

Later, before stage 5: agree durable event storage/API, audience/visibility, edits/cancellation,
retention, and who confirms timetable suggestions. Recommendation: separate event records and
additive documented API, timetable suggestions confirmed by headman/deputy through existing writes.
Do not change frontend or public contracts before coordination.

Before any real AI observation or stage 7: agree provider/model, monetary caps, allowed corpus,
groups and retention. Production deployment, writer mode, historical-card updates and rollout
scope are separate approvals. Numeric quality/cost/latency thresholds follow measurements.

## Implementation stage log

Owner accepted stage 0 and authorized sequential offline implementation on 2026-10-26.
The temporary publication policy in ADR-013 is now approved. Remaining product decisions are
collected at the end of this living report; no per-stage owner approval is required.

### 1a — PASS

Added strict internal image evidence/reading contracts, per-pass jobs and per-attempt ledger
(`0040` additive migration). Public APIs and old C+ snapshots are unchanged. Reviewed uniqueness,
foreign-key retention, pass identity, strict parsing and locking. 13 contract/PostgreSQL checks
passed; migration upgrade/downgrade to 0039/re-upgrade passed. Ruff clean after fixing a test
literal. Actual AI cost USD 0. Next: job scheduling and full image coverage.

### 1b — PASS

Added an independent, allowlisted image queue, disabled by default; images/documents and every
album member receive durable jobs. Six is no longer the new path's completion boundary. Legacy
photo behaviour remains available only when the new feature is off. Streamed Telegram downloads
retain sanitized temporary/terminal errors and reject path escapes/oversized files. Three bounded
attempts, source-revision invalidation, restart reuse and committed pre-call reservations added.
Review covered retention, group isolation, lock order and ambiguous external failures. 65
worker/photo regressions and 28 image/ingestion/contract/PostgreSQL checks passed; lint/diff clean.
Actual AI cost USD 0. The compatibility reader is still one pass in this commit; next is 2a.

### 2a — PASS

Added strict structured reading with text/blocks/table rows/facts/uncertainty, multi-label semantics,
schedule rows and event fields. Both versioned visual prompts send the original pixels; B also
receives saved A and group-local messages/replies/roles/schedule/existing-card context. No string
slicing; invalid/truncated responses explicitly fail with usage preserved. Pass B cannot silently
drop A's facts. Each pass commits independently. 20 image contract/worker/real PostgreSQL checks
passed; reviewed prompt injection boundaries, field constraints and feedback from generated cards.
Existing-card changes do not self-trigger unlimited image reanalysis. Real OCR/classification
quality remains UNMEASURED. Actual AI cost USD 0. Next: accounting and recovery hardening.
