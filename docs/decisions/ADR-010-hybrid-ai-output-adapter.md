# ADR-010: Hybrid AI output adapter and bounded structural repair

Status: accepted

Date: 2026-10-13

## Context

Two real C+ canaries completed both provider passes yet accepted no card. Reproduction showed two
independent causes:

1. `StrictDTO` models are `strict=True`, but the validator fed them `json.loads` output. In strict
   Python mode an ISO string is not a `datetime` and a UUID string is not a `UUID`, so every
   non-null `deadline.at`, every `existing_item_id`, and any integral float failed validation.
2. The Pass 2 prompt never defined how to express a date without time. A model that emitted
   `{"at": null, "date_only": true, "basis": "explicit"}` hit the `DeadlineValue` validator,
   reported as `proposals[n].deadline: value_error` (the validator rule was not recorded).

## Decision

- Pydantic contracts stay strict and remain the only source of truth.
- `analysis/output_adapter.py` converts only unambiguous provider shapes before strict
  validation: ISO/naive/date-only text to aware datetimes in the group timezone, UUID text,
  integral floats and numeric text, enum spelling, a unique revision lookup, harmless
  informational keys and the `source_id`/`message_id` evidence aliases, a derived
  `explicit_empty`. It never invents a subject, date, source or fact path; contradictory input
  (`basis` without a value, date-only with a clock time, ambiguous confidence scale, mutation
  without a trusted identity) is rejected with a stable, value-free code. Each proposal is
  independent, so valid siblings survive.
- Written calendar dates in cited sources outrank a model date: a proposal that ignores every
  literal date is tagged `conflicting_deadlines` and held for review; a deadline more than a day
  before its source is tagged `deadline_before_source` and held.
- `value_error` issues now record our own validator rule code (`CONTRACT_RULE_CODES`), never
  provider text.
- The Pass 2 prompt is generated from the models (`contract_prompt.py`): field signature, enums,
  nullability and valid examples cannot drift. Semantic rules JSON Schema cannot express are
  stated once. Prompt version `cplus-extraction-2` adds per-message `local_date`/`local_weekday`
  and a trusted `existing_items` list for update, reschedule and cancel.
- AI Repair (`repair.py`) is a single bounded fallback for structurally fixable rejections. It
  receives the rejected item, value-free rule codes and only the cited messages; its output goes
  through the adapter and the full validator again and must preserve every fact (texts, dates,
  audience, kind, cited sources); it may drop a contradictory deadline claim but never supply a
  value. Repaired cards carry `ai_structural_repair` and need headman attention. At most one call
  per extraction response, no recursion, inside the shared package cap, feature flag
  `AI_OUTPUT_REPAIR_ENABLED` (default off). This narrows ADR-009, which allowed repair only for
  unparseable JSON.
- Window backfill and promotion (`backfill.py`, `analysis_backfill` CLI) use stored RawMessages
  only, are idempotent and resumable, default to dry-run, link a repeated discussion to its
  existing item by cited-source evidence, skip expired items and emit no notifications.

## Consequences

Accepted counts now reflect real model content, and every rejection is explained by a code.
Prompts grow by about 2k cacheable input tokens. Update, reschedule and cancel proposals still
need a trusted `existing_items` list; shadow and backfill runs cannot supply one before
promotion, so such messages are reported as `mutation_identity_missing` for owner review rather
than guessed.
