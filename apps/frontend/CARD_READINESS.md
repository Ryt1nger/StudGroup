# Readiness of the Mini App for published learning cards

Status: implemented student API integration

Date: 2026-10-14

Scope: published cards only; shadow output is not a frontend data source

## Screen audit

| Screen          | Current API                             | Supported now                                                                                                                                                                     | Deliberately deferred                                                  |
| --------------- | --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| Today           | `GET /today`                            | generated title, confirmed subject or no subject, deadline or explicit absence, publication/cancellation/update/clarification/completion labels, loading/empty/error/stale states | material/source counts in summaries                                    |
| Tasks           | `GET /homework`                         | the same summary card, filters, cursor pagination, archive presentation, empty and partial-page error states                                                                      | no blocker for published homework                                      |
| Homework detail | `GET /homework/{id}`                    | structured description, public card kind, deadline certainty and origin, multiple sources, typed materials, latest published change, status and personal completion               | authorized inline file preview/download; full public revision timeline |
| Deadlines       | `GET /deadlines`, `GET /deadlines/{id}` | test/control point/assessment/credit/exam/other type, confirmed subject, certainty, sources, materials and latest published change                                                | personal completion, because it is homework-only by product rule       |
| Subjects        | `GET /subjects`                         | group-scoped subject names and published homework/deadline counts with loading/empty/error states                                                                                 | subject detail route independent of a lesson; nearest meaningful item  |

The detail screen opens Telegram only through a backend-built `https://t.me/...` URL. The client rejects a different scheme, hostname, credentials or port. An unavailable, deleted or inaccessible source remains a normal readable card state.

## Compatibility boundary

`src/features/homework/cardViewModel.ts` keeps the generated OpenAPI type as the source of truth. New fields are optional, so the deployed frontend can still read the preceding response shape. New responses retain legacy `type`, `subject` and `source_detail` fields during the compatibility window.

```json
{
  "card_kind": "homework | test | control_point | assessment | credit | exam | other_academic_deadline",
  "confirmed_subject": "SubjectRef or null",
  "deadline": {
    "state": "known | unknown",
    "at": "ISO timestamp or null",
    "date_only": true,
    "certainty": "confirmed | from_source | inferred | needs_clarification | unknown",
    "origin": "explicit_message | schedule_inferred | ambiguous_message | manual | imported | unknown"
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
      "created_at": "ISO timestamp"
    }
  ]
}
```

These fields are implemented in OpenAPI 0.9.0 and generated shared types. Enrichment is read-only and requires an existing `CanonicalProjectionLink`; it cannot publish a card. Evidence is read only for the projected revision. A shadow-only canonical item therefore remains unreachable from every student endpoint.

## Remaining backend agreements

1. Decide whether the public change history should contain only the latest projected revision (implemented) or an ordered sanitized timeline. No shadow or unpublished revision may enter that timeline.
2. If inline attachment previews are wanted, add a short-lived authorized file endpoint. The current API intentionally links attachment metadata to its Telegram source and never returns Telegram file identifiers.
3. Decide whether `/subjects` needs a dedicated detail endpoint and nearest-event/material counts. The overview is sufficient for the current list screen.
4. Decide when the legacy `type`, `subject` and `source_detail` fields can be deprecated. They must remain until older clients are outside the supported window.

Only cards returned by the existing student-facing endpoints are rendered. The frontend does not call extraction, shadow, review, backfill or promotion endpoints and does not implement a publication path.

## Minimal API handoff matrix

| Student API                              | Implemented projection                                                                                        | Remaining optional work                                   |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| `GET /today`, `GET /homework`            | title, `card_kind`, confirmed subject/null, deadline certainty/origin, status and personal state              | optional material/source counts                           |
| `GET /homework/{id}`                     | structured description, ordered sources, typed material metadata and latest student-safe update               | authorized inline attachment URL; full sanitized timeline |
| completion mutation for `/homework/{id}` | unchanged set/undo behavior with revision conflict recovery                                                   | none                                                      |
| `GET /deadlines[/{id}]`                  | actual C+ semantic type, confirmed subject/null, certainty/origin, sources, typed materials and latest update | none for the current detail UI                            |
| `GET /lessons/{id}/subject`              | related homework and deadlines inherit the same enriched representations                                      | none                                                      |
| `GET /subjects`                          | active-group subject identity plus published homework/deadline counts                                         | optional subject-detail endpoint                          |

The backend projection must expose only promoted student-safe data. Shadow candidates and review-only evidence remain outside these APIs.

## Covered states

- no deadline and confirmed deadline;
- date requiring clarification (when the viewer is allowed to receive that state);
- one or multiple current sources backed by projected evidence;
- multiple photos/documents/links without exposing Telegram file identifiers;
- significant update, structured additions/changes, cancellation and personal completion undo;
- unavailable Telegram source and absent materials;
- list/detail loading, empty, not-found, gone, forbidden, service error and retry.

Local MSW data remains explicitly mock data. Backend E2E tests create isolated published projections and assert that shadow-only and foreign-group records are absent.
