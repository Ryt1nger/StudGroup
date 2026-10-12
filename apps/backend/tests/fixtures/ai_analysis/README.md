# AI analysis evaluation fixtures

This directory contains only synthetic, de-identified fixtures and the schema for a private
Telegram gold corpus. Real chat exports belong in the ignored `private/` directory and must
not be committed.

## Split policy

Split by `discussion_id`/`entity_id`, never by individual message. All messages that can
describe, clarify, correct or cancel the same academic item must remain in one split. The
held-out evaluation split is not used for prompt development.

## Required annotation

Each JSONL row validates against `gold-corpus.schema.json` and records the source revision,
expected zero-or-more cards, field-level evidence, date basis, audience label and expected
action. Negative rows state why no card should be produced. Unknown values stay `null`; an
annotator must not reconstruct absent source messages from memory.

The private corpus validator and adjudication workflow are implemented in PR-0 before the
corpus is used as release evidence.
