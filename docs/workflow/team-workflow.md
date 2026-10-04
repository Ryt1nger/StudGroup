# Team workflow

## Responsibilities

| Area | Owner | Final acceptance |
| --- | --- | --- |
| Product scope and priority | Product owner | Product owner |
| Backend, bot, AI, data, integrations | Codex | Product owner |
| Frontend and WebApp | Claude | Product owner |
| Visual design, mockups, and assets | ChatGPT designer | Product owner |
| Shared API contracts | Codex proposes, Claude reviews | Product owner |

## Branches

- `main`: protected, accepted work only.
- `backend/<topic>`: backend changes.
- `frontend/<topic>`: frontend changes.
- `contracts/<topic>`: shared-contract proposals.
- `docs/<topic>`: product and architecture documentation.

## Pull requests

Every pull request must state:

- what changed;
- why it changed;
- affected contracts;
- tests performed;
- risks or follow-up work;
- product acceptance criteria.

Do not combine unrelated backend and frontend work in one pull request.

## Handoff

1. Product owner approves the behavior.
2. Codex publishes or updates the contract.
3. Backend and frontend work from that contract.
4. Contract and integration tests run.
5. Product owner accepts the result.
