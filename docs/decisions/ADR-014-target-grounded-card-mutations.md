# ADR-014: Target-grounded card mutations

Date: 2026-10-27

Status: accepted technical safety rule; production activation remains separately controlled.

## Context

A package contains new target messages and older read-only context. Pass 2 previously received the
messages without their roles, and validation accepted evidence from anywhere in the envelope. A
new empty target could therefore cause an existing card to be rewritten from an old source alone.
The morning production logs prove five successful updates, but stored revisions are still needed
to determine which, if any, were instances of this defect.

## Decision

Pass 2 receives `target_message_ids` and a `context_role` on every message. Every live create or
mutation must cite a target. Updates additionally require a content-bearing overlap between the
proposed card delta and target evidence; citing a target without support is rejected before
publication. Existing item descriptions are supplied whole rather than silently cut at 600
characters. The resolver continues to require the current item revision and row lock.

An ordinary update preserves an already inferred next-lesson deadline. Recomputing inference at a
later package boundary is not a reschedule; an explicit reschedule remains a separate action.

Historical stored evaluations without item snapshots remain parse-compatible. Their results are
not granted new publication authority by this compatibility rule.

## Consequences

- old-context echo and a formal citation of an empty target cannot mutate a card;
- real additions/corrections still update the same identity and do not create duplicates;
- stale revisions, retries and concurrent workers retain the existing transactional guards;
- a terse or semantically unclear mutation can be rejected conservatively and reviewed rather
  than changing student-visible state;
- full descriptions improve fact preservation but increase prompt size. The existing limit of 30
  recent active cards remains an operational bound; provider context-limit failures are explicit
  package failures, never silent text truncation.

Integration into Core v2 preserves image fact-reference checks after album reconciliation. Prompt
17 combines the image provenance contract with explicit target/context roles and the stricter
publication guard for unsupported fallback cards. An explicit terse
cancellation may bypass the minimum-new-words check only when it directly replies to a persisted
source of the same item. Thanks and unlinked terse cancellation remain held; this is a constrained
mutation guard, not a general chat classifier.
