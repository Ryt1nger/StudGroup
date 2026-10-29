# AI analysis evaluation fixtures

This directory contains synthetic/de-identified fixtures, explicitly licensed public academic
camera photographs and the schema for a private Telegram gold corpus. Real chat exports belong
in the ignored `private/` directory and must not be committed.

## Complex image benchmark

`image-complex-manifest.json` freezes 23 images and independent gold for 16 scenarios. Gold was
committed in 731c0b6 before provider requests. Two public camera originals and three derivatives
retain author, source URL, exact hashes and CC BY-SA 3.0/4.0 attribution in the manifest. The other
images are authored printed sheets or fictional screenshots, never claimed as camera captures.
`tools/build_complex_image_fixtures.py` reproduces authored sheets using the documented font;
camera c01/c02 must already be present. `test_complex_image_fixtures.py` verifies every hash and
required annotation field without a provider call. Original synthetic observations stay unchanged.

`tools/complex_vision_check.py` requires the original private USD-1 journal, retains the previous
USD 0.181507560 baseline, and globally stops HTTP on any new unknown paid outcome. Different prompt
revisions have separate result namespaces; all actual attempts remain charged in the same journal.
`--retry-known` only retries bounded, proven zero-charge connection failures. `--offline` reconstructs
current validation from exact stored responses and raises before any uncached HTTP request.

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
