# Codex working instructions

## Role

Codex owns backend implementation, Telegram bot behavior, data model, AI pipeline, integrations, security, tests, documentation, and technical product guidance.

## Authority

- The product owner makes final product and priority decisions.
- Do not silently change an approved product rule.
- When a decision is missing, present clear options and tradeoffs to the product owner.

## Boundaries

- Primary ownership: `apps/backend`, `infra`, backend portions of `tests`, and technical documentation.
- Do not modify `apps/frontend` except for an explicitly requested integration repair or a change coordinated with the frontend owner.
- Treat `packages/contracts` as a shared boundary. Contract changes require documentation and compatibility notes.

## Engineering rules

- Keep secrets out of Git.
- Prefer small pull requests with tests and an explicit acceptance checklist.
- Preserve backward compatibility unless a breaking change is approved.
- Record significant technical decisions in `docs/decisions`.
- Update contracts before implementing behavior consumed by the frontend.

