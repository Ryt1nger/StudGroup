# ADR-013: Versioned visual evidence inside C+

Status: implemented; authorized synthetic-provider validation completed; production activation requires owner approval.
Date: 2026-10-26.

Keep C+ context/extraction, canonical writer, source revisions and human locks. Add durable
per-image, per-pass jobs and an independent per-attempt accounting ledger. A technical retry
reuses a committed successful pass; another photograph always gets its own analysis. Pass B
receives pixels, saved pass A and a versioned group-local context snapshot. Missing/uncertain
facts never become confirmed facts by default. Similarity affects display only, after extraction.

Jobs retain their source/context fingerprint, lease token, bounded attempts and explicit error.
A crash after a provider accepted a request but before its result committed is ambiguous: retain
its conservative charge, never claim exactly-once external billing. Do not retry an expired paid
lease automatically; mark it for explicit review, preserving the budget reservation as a charge.

New processing is disabled by default and requires an explicit group allowlist. Shadow mode
records evidence without reopening messages or changing public cards. Public event publication
and timetable writes are independently gated. No production actions follow from this ADR.

Approved owner policy: defer unfinished images; retain terminal errors; independent confirmed
text may still flow through existing C+ rules. Never publish facts dependent on unreadable images.

## Owner decisions — 2026-10-27

The owner accepted the offline implementation and authorized at most USD 2 total for the existing
provider/model, only for the 16 committed synthetic PNGs. Verify documented vision support,
current rates and a durable pre-call budget before starting. Do not switch paid models, use
private chat data, deploy, activate real groups/writers or process history. Unknown-paid attempts
consume their conservative reservation; a retry cannot reset the USD 2 allowance.

Separate group events and an additive public API are approved in principle. Editor confirmation
and confirmed group audience are mandatory; public flags remain false until frontend integration
and separate activation approval. Reviewed event data survives source expiry. A published event
is retained through the group's local end date + 7 calendar days inclusive: delete_at is the next
local midnight (end date + 8 days, converted to UTC), using an explicit end if supplied, else the
start date/calendar day. Known multi-day events require an end. Unknown dates cannot publish.
Cancellation hides the event from active lists and retains its approved expiry; a date change
requires editor confirmation and recalculates expiry. Undated drafts retain bounded source expiry.
Private source messages, image readings and unapproved findings keep their original expiry and
are never extended or copied wholesale into the event. Public reads use the approved structured
snapshot, not the continued presence of private sources. Expired source links are unavailable.

Schedule confirmation must allow an authorized editor to correct uncertain subject, subgroup,
effective dates, replacement mapping, time, room, teacher and once/recurring scope. Use group-local
IDs, official revision locks and the existing schedule audit. No ambiguous automatic application.
Backend contracts are prepared here; frontend changes and production activation are separate work.


## Validation safeguards — 2026-10-27

The context snapshot identifies the exact image source message and album/reply relations.
Pixel facts never include chat-only claims; chat changes purpose and required actions with its
own source IDs. The C+ request supplies a copyable fact-reference index. Bare-image proposals
require exact image fact references, and the check repeats after album reconciliation so a
newly attached unread/uncited page cannot bypass the publication gate. Missing provenance is
held for review; prompts are reinforced without weakening this gate. Unknown purpose is distinct
from unreadable pixels. "Finish the rest" never authorizes reassigning the entire printed range.
The synthetic run, including failed schema responses and bounded retests, is recorded in the
canonical integration report; its expense never resets the single authorized USD 2 ledger.

## Display coverage after semantic merge — release continuation 2026-10-27

Display minimization uses source-retained local ink/word geometry, binary word pixels, complete
structured readings and a readability floor. Every visible ink row must map to a transcription
line. Matching OCR or a perceptual hash alone is no longer sufficient to hide a structured image.
Only explicit exercise ordinal ranges may expand; arithmetic subtraction, individual exercise
numbers, rooms, equations, tables and untranscribed diagrams cannot use that rule. Unknown or
conflicting readings/layouts retain all images. Full semantic evidence and source attachments
remain unchanged. Exact-byte duplicates remain safe to collapse for display.

Coverage derivatives expire with their source jobs (additive migration 0045), never extend
private-source retention, and are not sent to the model. Immutable snapshots carry only compact
coverage edges for consistent preview/student display. Selection finds a minimum cover for up
to 16 candidates, with a safe greedy cover for larger batches. None of this starts an AI call.

## Model-aware source-bounded reuse — 2026-10-27

Migration 0046 records a provider/model/revision/schema digest and reuse origin. Cross-source
pass-A reuse requires explicit image_model_revision configuration, exact bytes or Telegram stable
file identity, matching prompt/schema/model, current source retention and the same group. Default
revision is empty and cross-source reuse is off. Caption revisions reuse only unchanged pixels;
pass B still receives the current context and pixels. Provider identity participates in both keys
but is not sent as reasoning context. Successful exact-input snapshots can become current again;
failed/ambiguous jobs cannot. Reuse never extends expiry or crosses group boundaries.

The owner's later instruction supersedes the former USD 2 ceiling: all old observations and
unknown-outcome reservations remain accounted inside one USD 1 total for comparisons and pilot.

Manual projection changes (schedule version/rows/catalog, existing cards and events) do not
invalidate the paid image source key. They enrich the initial request; official schedule revision
checks and current C+ catalog validation remain separate guards. Exact standalone “Спасибо!”
without additional facts is retained in raw/C+ evidence but omitted from an older image's context
unless it is the image source or a needed reply bridge. Short cancellations, digits and messages
with additional words remain meaningful. These are source-identity/cost rules, not publication
classification.

Editor re-review after an unrelated official schedule change is explicit, not a paid visual retry.
An optional acknowledgement plus full corrected rows and current expected schedule version allow
the existing authorized confirmation path to proceed. Without all three, the original stale-version
guard is unchanged. Source validity and row/subject/subgroup/occurrence locks still apply. Both
source and applied versions and acknowledgement are stored with the manual confirmation.
