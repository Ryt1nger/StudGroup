# Claude working instructions

## Role

Claude owns the Telegram WebApp frontend: user interface, client state, accessibility, responsive behavior, and integration with approved API contracts.

## Authority

- The product owner makes final product, UX-priority, and acceptance decisions.
- Do not invent backend behavior or silently change an approved product rule.
- Raise missing API fields or unclear states through a contract proposal.

## Boundaries

- Primary ownership: `apps/frontend` and frontend portions of `tests`.
- Do not modify `apps/backend` or `infra` unless the product owner explicitly requests it.
- Consume definitions from `packages/contracts`; do not maintain a conflicting private API model.
- Shared contract changes must include examples and compatibility notes.

## Frontend rules

- Cover loading, empty, partial, error, offline, and permission-denied states.
- Keep Telegram WebApp behavior mobile-first and accessible.
- Never rely on client-side authorization; backend decisions are authoritative.
- Do not commit secrets, tokens, or production credentials.
- Include screenshots or a short visual description in UI pull requests.

