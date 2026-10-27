# ADR-013: Versioned visual evidence inside C+

Status: accepted for offline implementation; production activation requires owner approval.
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
