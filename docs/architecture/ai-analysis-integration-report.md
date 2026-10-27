# AI analysis integration report

Updated: 2026-10-27. Scope: stages 0–7 of the Core v2 image-quality brief; evolutionary C+ changes only.
Owner decisions of 2026-10-27 are implemented; synthetic-only paid checks are authorized up to $2 and recorded below. Real-group activation and production deployment are not approved.
Source: `origin/main` at `dbc59638bcde89daec29e2d71c6c51bb95c29d54`.
Working branch: `codex/core-image-audit`, in a separate managed worktree.

## Baseline and scope

The original checkout is `codex/ai-analysis-core`, one commit ahead and 82 behind current
`origin/main`, with unrelated uncommitted files. Local `main` is 20 commits behind
`origin/main`. Fetch succeeded. The audit uses current remote main without merging, resetting
or changing the original checkout. At baseline no production configuration, provider call, published card, frontend file or shared API contract had been changed. Subsequent additive contracts and authorized synthetic calls are described below. Obsolete documentation was not used.

This report replaces the old integration snapshot in place. The implementation plan is in
[the current roadmap](ai-analysis-rebuild-roadmap.md#18-core-v2-image-quality-work).
The system already has a live package writer, durable shadow execution and canonical projection;
the earlier integration report's missing-writer blockers no longer describe this source revision.

## Stage-0 baseline source-to-student path

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

Stage verdict: **PASS for the offline technical audit**. Owner accepted stage 0 and authorized implementation on 2026-10-26. Product image quality is not accepted by this audit; model
classification and pixel-reading accuracy are unmeasured. At the stage-0 checkpoint no implementation fix had shipped.
No paid model calls were made; no deployment or writer-mode switch was performed.

## Current authorization

Owner accepted the baseline and authorized all safe sequential technical work on 2026-10-26,
including the temporary unreadable-image publication policy. No further per-stage confirmations
are required. Paid AI calls, real-photo transfers to a new provider, production deployment,
writer-mode changes and mass historical processing remain unauthorized. Current owner decisions
are collected once at the end of this report.

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

### 2b — PASS

Shared group/day and rolling-hour accounting now includes both image and text reservations under
the existing group settings lock. Added per-image and per-album/message batch caps, conservative
ambiguous-call settlement, usage/latency per attempt and stale-context/pixel guards. A superseded
pass still settles its real usage. Pass B retries reuse committed A. Expired paid leases stop for
review instead of automatically paying twice. 51 image/shadow/PostgreSQL checks passed, including
zero budget, all caps, crash/lease recovery, edit during paid call and preserved costs across a
B retry. Ruff/diff clean. Reviewed transaction lock order and data isolation. No exact-once
external billing claim: a process crash can leave an explicitly unknown paid outcome. USD 0 spent.

### 3 — PASS

C+ receives typed evidence for every image (source/attachment/revision/fact index); live and shadow
use the same loader. Confirmed visual facts support card/date validation without rewriting raw
source text. Added strict image fact-reference checks and guards against classwork/unknown/poster/
schedule becoming homework; independent text remains eligible when an image fails. Unfinished
sources use explicit image_pending state and reopen when new evidence arrives. Updated extraction
prompt to retain precise confirmed numbers and conditions. 129 initial regressions and 111 final
semantic/hybrid/stored-result/PostgreSQL checks passed. Fixed a test DTO serialization mistake
and the adapter's old fact-path whitelist, then reran successfully. Review covered evidence
scope, retry review-candidates, prompt compatibility and manual source preservation. USD 0 spent.

### 4 — PASS

Added local bounded Pillow visual hashing plus exact pixel hashes. A shared display-only selector
uses visual proof and identical structured content; different facts/pages/crops stay visible.
Explicit card image references exclude unrelated images from the same source. No fixed image
count. The analysis snapshot still contains every image, including hidden exact duplicates.
Live/shadow snapshots carry display metadata; owner preview and student API share selection.
Structured images never replace the full card description. Compatibility notes updated; no
frontend edits or public schema removal. Reviewed per-card source references and image-bomb
handling. 53 initial and 34 display/preview/student API/PostgreSQL checks passed; lint/diff clean.
Selection is deliberately conservative when visual equivalence cannot be proved; real-world
suppression quality remains UNMEASURED. Actual AI cost USD 0.

### 5a — PASS (technical implementation; pilot/UI remain gated)

Added image_schedule_suggestions (`0041`) with source job, official schedule version, typed rows,
revision and retention. Staff-only gated list/confirmation endpoints recheck role, source/context,
period, week anchor and current timetable version. New rows and exact slot updates use existing
manual decisions/audit/deadline rebuild; explicit occurrence mappings use existing once/recurring
change logic and headman locks. Retry confirmation is idempotent. Missing dates, ambiguous lesson
mapping and unmapped subgroups are blocked rather than guessed. Added draft contract in
`packages/contracts/image-findings.openapi.yaml`; no frontend changed. Reviewed role revocation,
transaction atomicity and non-automatic writes. 35 focused regressions and final 8 real PostgreSQL/
API cases passed; 0041 downgrade/re-upgrade, lint and diff checks passed. Actual AI cost USD 0.
Owner/UI choice still needed for subgroup mappings; official timetable never changes automatically.

### 5b — PASS (technical implementation; public contract activation BLOCKED)

Added separate group_events, per-source applied/held findings and immutable revisions (`0042`).
Existing EventRecord candidates map compatibly to drafts; image readings provide richer date,
place/audience/conditions. Concurrent duplicate posters deduplicate under a group lock. Model
updates/cancellation require identity/revision; published or manually locked records retain the
incoming change for review. Staff update/cancel and optional explicit publication/list/detail
endpoints use separate default-off flags plus group allowlist. Personal/unknown sources never
publish to the whole group. Retention is bounded by source expiry; periodic cleanup also runs
when feature flags are off. Updated canonical event contract and additive pilot API specification.
Reviewed source isolation, stale sources, manual locks and prevention of shadow-driven changes to
legacy card display (published canonical image provenance is required). Fixed a PostgreSQL test's
expired ORM fixture reference and title-less candidate handling. Final 60 lifecycle/candidate/
PostgreSQL checks passed; 0042 downgrade/re-upgrade, lint/diff clean. Actual AI cost USD 0.
Owner decisions on public API/frontend, retention and subgroup visibility remain; no real publication.

### 6 — PASS

Full offline image -> two visual passes -> C+ -> canonical card -> student API is tested, including
precise confirmed numbers, complete text alongside images, idempotent reruns and shadow isolation.
Added explicit activation start timestamp to prevent implicit historical processing. Current source/
context checks reject stale readings; stored prompt versions participate in job identity. Preserved
legacy snapshot hashes when new optional fields are absent. Kept legacy context metadata JSON-safe.
Oversized semantic input is explicitly held as needs_context with an incident; full paid image
results remain stored, never truncated. Teacher/location/catalog reference data reaches pass B.

The first rerun found two legacy /explain failures after the date rollover: its old-source + future
schedule span exceeded the public 32-day limit. Fixed internal non-overlapping calendar chunking
without weakening that limit. A shadow-isolation assertion now excludes only generated_at, which
changes on every API read. Oversize fixture serialization was corrected to match a real Unicode
JSON answer. All failures were corrected and rechecked; no tests disabled or expectations weakened.
Final full command (backend cwd): `POSTGRES_TEST_URL=<disposable-local-db> uv run pytest -q`:
**1172 passed, 0 failed, 0 skipped**, 150.93 s; one upstream Starlette deprecation warning.
An additional 13 integration/schedule/event checks passed after adding catalog context. PostgreSQL
17 migration cycle in a separate disposable database: base -> 0042 -> base -> 0042 succeeded.
Ruff and diff checks clean. Manual lock, notification, group-isolation and concurrency regressions
are included in the full suite. Actual AI cost USD 0. Next: offline evaluation and rollout preparation.


### 7 — PASS for offline preparation; real vision evaluation BLOCKED

Added offline-only `image_evaluation.py`: render synthetic PNGs, evaluate recorded observations,
compare old/new only for identical pixel + chat/annotation input fingerprints. Missing observations
produce null metrics and UNMEASURED, not fabricated accuracy. Exact normalized fact annotations
and optional adjudicated checks cover facts, purpose, classwork/homework counts, schedule/event
fields, display, linking and unique-fact preservation; semantic paraphrases need adjudication.
Created 16 printed synthetic PNGs for 11 scenarios, with a versioned manifest. They are not a
handwriting/real-world benchmark. Visually inspected the rendered Cyrillic schedule fixture.
Added private feature-gated staff image diagnostics: states, sanitized errors, attempts, versions,
usage/cost/reservations/unknown outcomes and latency; no image bytes or raw model text. Added
activation/shadow/rollback instructions to backend README. All flags remain off, group allowlist
empty and start timestamp unset. Budget exhaustion defers jobs rather than making them irrelevant.
A final PostgreSQL check found a group with no subject catalog; fixed pass-B context to carry null
instead of raising. Nine focused PostgreSQL/evaluation checks passed after this fix.
Final regression command (backend cwd): `POSTGRES_TEST_URL=<disposable-local-db> uv run pytest -q`.
**1178 passed, 0 failed, 0 skipped**, 144.46 s. Ruff and diff checks passed. Both OpenAPI YAML
contracts parsed (31 existing paths, 7 additive pilot paths). The final full run also caught a
legacy test helper selecting superseded bot frames by timestamp. Updated it to match the actual
worker, which never displays superseded rows; added a regression with a deliberately later stale
frame. All 17 headman checks and then the complete suite passed without disabling tests.
Stage commit: `979e5ed`.
Actual paid API calls: 0. Actual AI expenditure: USD 0. No production or public publication actions.

## Сводный результат для владельца — 27 октября 2026

Реализация сохраняет C+, существующий writer, ревизии, ручные блокировки и прежние API.
Новая очередь учитывает все поддерживаемые JPEG/PNG/WebP, включая документы и альбомы.
Каждое изображение получает два визуальных прохода с отдельными сохранёнными результатами и
попытками. После сбоя завершённый проход повторно не оплачивается; неизвестный исход оплаченного
запроса фиксируется отдельно и автоматически не покупается снова. Ошибки и исчерпание бюджета
не считаются несущественными изображениями. Независимый текст сохраняет обычный путь C+.

Все изображения участвуют до отбора материалов. Подтверждённые факты ссылаются на конкретное
вложение/сообщение/ревизию, поддерживают точные числа и даты. Работа на паре и другие явно не
домашние категории защищены от публикации как ДЗ. Расписание поступает в предложения с
авторизованным подтверждением и аудитом; афиши — в отдельные черновики событий с источниками,
ревизиями и обновлением/отменой. Frontend не изменялся. Новые публичные разделы выключены.

### Проверяемая готовность по этапам

| Этап | Технический результат | Коммит | Основная проверка |
|---|---|---|---|
| 1a | PASS: контракты, задания, журнал вызовов, миграция 0040 | a3c42b6 | 13 contract/PostgreSQL; downgrade/re-upgrade |
| 1b | PASS: охват 1/6/7/12, документы, очередь, повторы, конкуренция | e845ad4 | 65 regressions + 28 focused checks |
| 2a | PASS: два запроса с пикселями, структуры и сомнения | be9ba3b | 20 image/contract/PostgreSQL |
| 2b | PASS: восстановление и предварительные общие резервы | 0ef3649 | 51 failure/budget/concurrency checks |
| 3 | PASS: факты всех фото в C+, ссылки и ограничения публикации | 592b352 | 111 final semantic/hybrid/stored/PostgreSQL |
| 4 | PASS: отбор для показа отдельно от анализа | 3256381 | 34 final display/preview/API/PostgreSQL |
| 5a | PASS: предложения и подтверждение расписания; UI/подгруппы ограничены | 71f565b | 8 final API/PostgreSQL, миграция 0041 |
| 5b | PASS: backend событий; публичное включение BLOCKED | 4fbb9c0 | 60 final lifecycle/candidate/PostgreSQL, миграция 0042 |
| 6 | PASS: сквозная интеграция, совместимость, изоляция | fd68be5 | 1172 full + 13 post-catalog checks; полный цикл base ↔ 0042 |
| 7 | PASS: офлайн-инструменты; реальное качество BLOCKED | 979e5ed | 1178 passed, 0 failed/skipped; lint/contracts clean |

Повторные прогоны не суммируются в число уникальных тестов. Подробные файлы и устранённые ошибки
перечислены в соответствующих записях этапов выше. PR не создавались; merge/push/deploy не выполнялись.

### Что подтверждено, а что ещё нужно измерить

- Охват 1, 6, 7 и 12 изображений/документов, два визуальных вызова, сохранение результатов,
  отсутствие потери подтверждённых обрезок до C+, уникальные страницы, защита источников и бюджетов
  подтверждены автоматическими проверками с локальными ответами провайдера.
- Точные дубликаты и визуально близкие изображения с одинаковым содержанием отбираются отдельно.
  При недостаточном доказательстве эквивалентности остаются оба изображения: минимальность набора
  для сложных обрезок пока не доказана. Жёсткого лимита в 1–2 картинки нет.
- ДЗ/работа на паре, расписания и события проверены на уровне контрактов и публикационных правил.
  Реальная способность vision-модели прочитать доску, сложную таблицу или отличить эти назначения
  пока UNMEASURED / AWAITING OWNER APPROVAL. Ответы заглушек этого не доказывают.
- Большие результаты не обрезаются молча. Если объединённые доказательства превышают безопасный
  размер C+ входа, источник явно удерживается для проверки; сохранённые чтения не теряются.
- Ручные изменения, ревизии, отсутствие повторных карточек/уведомлений, групповые границы,
  конкуренция и действующие текстовые сценарии входят в регрессионный набор.

### Финансы и ограничения включения

Нормальный новый путь: **2 vision-вызова на изображение**, затем существующие **2 текстовых
прохода C+ на пакет**. Относительно старого photo-reading это +1 vision-вызов на изображение.
Сохранение событий и предложений расписания дополнительных AI-вызовов не добавляет. Успешный A
переиспользуется при повторе B; ошибки ограничены тремя попытками на проход, неоднозначная оплата
требует проверки. Новые исходники/контекст могут требовать нового анализа в пределах лимитов.

Текущие резервные настройки: USD 0.02/проход, 0.12/изображение, 0.20/альбом или сообщение,
0.25/час плюс общий существующий дневной лимит группы с C+. Это предохранители в коде, а не
проверенная цена провайдера. Неожиданное превышение резерва фиксируется как ошибка с фактическим
usage. Стоимость/задержка реальных вызовов не измерены. Фактические расходы тестов — **USD 0**.

Код, миграции и офлайн-проверки готовы. Feature flags, диагностический API, предложения расписания,
review событий и публикация студентов выключены. Без разрешения нельзя ни включать production,
ни менять writer mode, ни переобрабатывать историю. Для ограниченного shadow-пилота нужны
утверждённые провайдер/тариф, бюджет, данные, группа и дата начала. Для студенческой публикации
дополнительно нужны решение по API/frontend и оценка реального качества с согласованными порогами.

## Решения владельца от 27.10.2026 — приняты

1. Разрешены настоящие AI-вызовы только для 16 существующих синтетических PNG,
   существующая совместимая модель, общий лимит $2. Реальные данные и production запрещены.
2. События публикуются отдельным разделом после подтверждения редактором групповой аудитории.
   Публичный API подготовлен; frontend и включение требуют отдельной координации/разрешения.
3. Опубликованное событие хранится до конца седьмого календарного дня после завершения
   по часовому поясу группы. Исходники удаляются по прежним срокам, независимо от события.
4. Редактор явно уточняет строки расписания и заменяемые занятия. Неоднозначное автоматическое
   применение запрещено; исправления проходят существующие права, ревизии и аудит.
5. Реальный пилот пока не разрешён. После измерений допускается только предложение плана.

## Утренний инцидент 27.10.2026 — read-only расследование

Период: 07:00:09–07:02:15 Europe/Moscow (04:00:09–04:02:15 UTC). Источник фактов —
Render runtime PACKAGE_RESULT; ключи и приватный текст переписки в отчёт не включены.
В production работает `f95c516` от deployment 24 октября. Новая локальная image-ветка не
развёрнута и не является причиной этого инцидента.

Подтверждено журналами: 11 completed-пакетов, каждый attempt=1, error=null. Сумма targets —
178 включений (уникальность исходных сообщений по этим строкам определить нельзя). Created=1,
updated=5; остальные 5 пакетов не изменили карточки. В создающем пакете rejected=1, причину
нужно читать из сохранённого validation_result_json. Учтённый расход всех пакетов USD 0.073992732;
шести пакетов с изменениями — USD 0.046189872, из них пяти обновлений — USD 0.034033560.
Это значения backend ledger, не независимая сверка списаний у провайдера.

Обнаружен и локально воспроизведён дефект допуска обновлений из одного старого контекста:

1. Первый пакет создаёт карточку из источника 31.
2. Новый target 32 — синтетическое «Спасибо!», без новых учебных сведений.
3. В окне второго пакета остаётся старый источник 31. Модель предлагает update карточки,
   ссылаясь только на 31 и лишь перефразируя описание.
4. Backend принимает такое предложение и сохраняет revision=2 / updated, хотя новая цель 32
   не подтверждает ни одного изменения. Провайдер в этом тесте локальный, реальных AI-вызовов нет.

Причины в коде работающей версии: Pass 2 получает messages, но не target_message_ids;
валидация требует присутствия источника в общем окне, без требования новой целевой причины
обновления. Старые и новые требования могут заново пересказываться; отличие строки считается
видимым изменением и порождает новое уведомление. _existing_items также ограничивает старое
описание 600 символами. Это воспроизводимый дефект, хорошо объясняющий симптом; без stored
input/validation/revision snapshots нельзя утверждать, что каждая из пяти утренних правок имела
именно такой набор источников или была лишней.

Read-only доступ к деталям ограничен: внешняя allowlist PostgreSQL пуста; прямое внешнее
подключение закрывается; internal hostname недоступен локально; SSH отказал publickey.
Разрешения, allowlist, deployment, writer mode и карточки не менялись. Для окончательной
корреляции нужен разрешённый read-only экспорт соответствующих ShadowEvaluationRun (input,
validation, metrics), CanonicalItemRevision и источников за этот интервал. Никаких новых платных
запросов ради расследования запускать не требуется.

Предлагаемое исправление: явно передавать Pass 2 текущие target IDs; перед применением update
требовать подтверждённую целевую причину/новый факт и сохранять старую карточку при простом
повторном пересказе. Нельзя ограничиваться скрытием уведомлений или запретом всех повторных
обновлений: настоящие дополнения, переносы, отмены и завершённое новое чтение изображения
должны по-прежнему обновлять существующую карточку. Production-патч не применялся.


## Принятые решения и заключительная проверка Core v2 — 27.10.2026

### Шаги 1–3: PASS

Решения владельца закреплены в ADR-013 и контракте 0.2.0. Добавлены даты/время окончания,
даты без времени и признак подтверждённого многодневного события. Миграция 0043 хранит
одобренный snapshot и срок жизни независимо от приватных источников. Первый publish требует
актуального группового источника; последующее чтение использует только одобренную структуру.
Опубликованное событие переживает очистку источника и доступно весь седьмой день после окончания.
Перенос пересчитывает срок; отмена скрывает запись из активного списка, сохраняя детали до того же
срока. Неизвестные/противоречивые даты не публикуются; черновики не стали бессрочными.

23 focused unit/API/PostgreSQL checks passed, 0 failed/skipped. Миграция 0043 downgrade/re-upgrade
прошла. Приватные источники и непринятые findings не продлеваются. Промпты A/B обновлены до версии 2;
старыми результатами новая схема молча не подменяется. Платных вызовов пока 0, все public flags false.

### Шаг 4: подтверждение расписания

Редактор может передать исправленные строки, явно сопоставить заменяемую пару и выбрать
разовое либо постоянное изменение. Поддерживаются предмет, даты действия, подгруппа,
время, аудитория и преподаватель. Проверяется принадлежность подгруппы предмету и группе,
ревизии и права; применяются существующие аудит и ручные блокировки. Исходный AI-результат
не переписывается. Для разовой замены предмета преподаватель прежней пары не наследуется.
Миграция 0044 аддитивная. Поле subgroup_id в публичном расписании необязательное.
Frontend-интерфейс подтверждения ещё требуется; флаги выключены.
