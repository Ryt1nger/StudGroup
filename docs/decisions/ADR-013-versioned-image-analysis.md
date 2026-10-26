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
