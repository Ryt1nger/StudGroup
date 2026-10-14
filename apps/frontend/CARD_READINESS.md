# Readiness of the Mini App for published learning cards

Status: current frontend audit and compatibility boundary

Date: 2026-10-14

Scope: published cards only; shadow output is not a frontend data source

## Screen audit

| Screen          | Current API                                                       | Supported now                                                                                                                                             | Still requires a backend contract                                                                                       |
| --------------- | ----------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Today           | `GET /today`                                                      | generated title, subject, deadline or an explicit unknown deadline, cancellation/update/clarification/completion labels, loading/empty/error/stale states | learning-card kind other than `homework`; typed material/source counts in summaries                                     |
| Tasks           | `GET /homework`                                                   | the same summary card, filters, cursor pagination, archive presentation, empty and partial-page error states                                              | no additional fields are required for the current summary; richer list metadata is optional                             |
| Homework detail | `GET /homework/{id}`                                              | title, description, subject, deadline, publication/cancellation/verification state, personal completion, one Telegram source, HTTPS material links        | card kind, deadline certainty/provenance, multiple sources, typed photo/document metadata, additions and change history |
| Subjects        | no subject-list endpoint; `/subjects` is an honest prepared state | a lesson context from `GET /lessons/{id}/subject` already shows related homework and materials                                                            | subject list/overview endpoint with stable subject identity, counts and nearest meaningful item                         |

The detail screen opens Telegram only through a backend-built `https://t.me/...` URL. The client rejects a different scheme, hostname, credentials or port. An unavailable, deleted or inaccessible source remains a normal readable card state.

## Frontend compatibility boundary

`src/features/homework/cardViewModel.ts` keeps the generated OpenAPI type as the source of truth. It maps the current response without changing it and can render the optional proposal below when those fields are later approved and generated from OpenAPI. Unknown values are ignored and never printed as technical codes.

```json
{
  "card_kind": "homework | control_work | test | credit | exam | project | presentation | other",
  "deadline": {
    "state": "known | unknown",
    "at": "ISO timestamp or null",
    "date_only": true,
    "certainty": "confirmed | from_source | inferred | needs_clarification"
  },
  "materials": [
    { "id": "stable id", "title": "visible title", "url": "https URL", "kind": "photo | document | link" }
  ],
  "sources": ["SourceDetail"],
  "updates": [
    {
      "id": "stable id",
      "kind": "supplement | change | cancellation",
      "text": "clean visible text",
      "created_at": "ISO timestamp or null"
    }
  ]
}
```

This is a compatibility proposal, not an implemented API contract. `packages/contracts` and generated types were deliberately not changed in this branch. Before the backend emits these fields, names and semantics must be agreed and added to OpenAPI with compatibility notes.

## Backend agreements needed

1. Define one card-kind field and its exhaustive public enum. The existing `type` is fixed to `homework` and must not be silently repurposed.
2. Separate deadline certainty/provenance from whole-card verification. A missing date must remain `state: unknown` with `at: null`; the backend must never manufacture a display date.
3. Return an ordered `sources` collection while retaining `source_detail` during a compatibility window. Every Telegram action remains backend-built and access-scoped.
4. Add material kind and, if inline previews are wanted, a short-lived authorized preview/download URL plus MIME type and safe display name. Telegram file identifiers alone are not usable by the browser.
5. Return ordered, student-safe additions/changes with stable IDs, timestamps and sanitized text. Do not expose shadow traces, prompts, evidence scores or internal statuses.
6. Add a subject list/overview response before replacing the prepared `/subjects` state.

Only cards returned by the existing student-facing endpoints are rendered. The frontend does not call extraction, shadow, review, backfill or promotion endpoints and does not implement a publication path.

## Covered states

- no deadline and confirmed deadline;
- date requiring clarification (when the viewer is allowed to receive that state);
- one current source and a compatibility fixture with multiple sources;
- multiple photos/documents/links through typed compatibility fixtures;
- significant update, structured additions/changes, cancellation and personal completion undo;
- unavailable Telegram source and absent materials;
- list/detail loading, empty, not-found, gone, forbidden, service error and retry.

The extended-card fixtures exist only in tests and are not presented as production data.
