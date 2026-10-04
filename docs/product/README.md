# Product specifications

Approved product rules and module specifications belong here.

Documents in this directory describe what the product must do. Technical implementation choices belong in `docs/architecture` and `docs/decisions`.

## Canonical documents

1. [Module 0: Product domain rules](module-0-domain-rules.md) is the approved source of truth for roles, cards, AI processing, notifications, retention, payments, referrals, and personal schedules.
2. [Module 1: Technical foundation and infrastructure](module-1-technical-foundation.md) is the approved source of truth for the technical foundation.
3. [Pilot success criteria](pilot-success-criteria.md) defines how the first external pilot is evaluated.
4. [MVP implementation boundaries for Modules 2–15](mvp-implementation-boundaries.md) records the decisions that unblock coding while deferring non-critical detail.
5. [WebApp screen-to-data matrix](screen-data-matrix.md) keeps backend data design aligned with the approved frontend screens.

PDF exports, mockups, chat discussions, and the earlier `StudGroup documentation v0.3` are supporting material. If supporting material conflicts with an approved Markdown specification, the Markdown specification in this repository takes precedence.

Approved behavior must not be changed silently. A product-rule change requires product-owner confirmation and a committed update to the corresponding module document.
