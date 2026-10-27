# AI analysis evaluation fixtures

This directory contains only synthetic, de-identified fixtures and the schema for a private
Telegram gold corpus. Real chat exports belong in the ignored `private/` directory and must
not be committed.

## Split policy

`image-scenarios.json` is the stage-0 image acceptance inventory. It has its own versioned
format (not the Telegram JSONL schema below). Its 11 cases contain synthetic image text
annotations and expected outcomes, not real pictures or measured model answers. They cover
crops, distinct pages, in-class/homework ambiguity, schedules, events, private invitations,
mixed entities, corrections and unreadable input. `test_image_analysis_baseline.py` reproduces
the deterministic failures using stub vision answers. Neither resource proves vision accuracy;
the rendered synthetic PNGs in `image-pixels/` now provide real deterministic pixel inputs.
The manifest hashes both pixels and the chat/annotation snapshot. These printed fixtures are
not handwriting or real-world image benchmarks; authorized provider measurements and a held-out
adjudicated corpus remain required before rollout.

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
