# Product specifications

Approved product rules and module specifications belong here.

Documents in this directory describe what the product must do. Technical implementation choices belong in `docs/architecture` and `docs/decisions`.

## Canonical documents

1. [Module 0: Product domain rules](module-0-domain-rules.md) is the approved source of truth for roles, cards, AI processing, notifications, retention, payments, referrals, and personal schedules.
2. [Module 1: Technical foundation and infrastructure](module-1-technical-foundation.md) is the approved source of truth for the technical foundation.
3. [Pilot success criteria](pilot-success-criteria.md) defines how the first external pilot is evaluated.
4. [MVP implementation boundaries for Modules 2–15](mvp-implementation-boundaries.md) records the decisions that unblock coding while deferring non-critical detail.
5. [WebApp screen-to-data matrix](screen-data-matrix.md) keeps backend data design aligned with the approved frontend screens.

Each specification above is a living document. Apply approved changes directly to the existing canonical file instead of creating versioned, `updated`, or `final` copies. Git history is the only revision archive.

`StudGroup documentation v0.3` and other historical product documents are obsolete. Do not review, reconcile, cite, copy, or use them as implementation input. Mockups describe visual intent only and cannot redefine approved product behavior.

Approved behavior must not be changed silently. A product-rule change requires product-owner confirmation and a committed update to the corresponding module document.
