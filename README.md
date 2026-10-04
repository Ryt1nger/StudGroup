# StudGroup

StudGroup is a Telegram bot and WebApp that turns group-chat messages, files, and schedule changes into structured academic information and useful notifications.

## Team

- Product owner, Project Manager, Product Manager: **Ryt1nger**
- Backend, architecture, AI pipeline, integrations, and product support: **Codex**
- Frontend and Telegram WebApp: **Claude**

The product owner makes the final decision on scope, priorities, and acceptance.

## Repository layout

```text
apps/
  backend/          Backend, Telegram bot, AI pipeline, payments
  frontend/         Telegram WebApp
packages/
  contracts/        OpenAPI and shared API schemas
  shared-types/     Generated or framework-neutral shared types
docs/
  product/          Approved product specifications
  architecture/     Architecture and data-flow documentation
  decisions/        Architecture Decision Records
  workflow/         Team process and handoff rules
infra/              Deployment and infrastructure definitions
tests/              Cross-application and end-to-end tests
```

## Working rule

Backend and frontend are developed independently against versioned contracts in `packages/contracts`. A contract change must be reviewed before either side relies on it.

Approved product behavior and technical decisions are recorded in Git:

- [Module 0: Product domain rules](docs/product/module-0-domain-rules.md)
- [Module 1: Technical foundation and infrastructure](docs/product/module-1-technical-foundation.md)
- [Pilot success criteria](docs/product/pilot-success-criteria.md)
- [Pilot vertical delivery plan](docs/architecture/pilot-vertical-slice.md)
- [MVP implementation boundaries](docs/product/mvp-implementation-boundaries.md)
- [WebApp screen-to-data matrix](docs/product/screen-data-matrix.md)

These Markdown files are canonical. PDF exports, mockups, and earlier documentation versions are supporting material and must not define a conflicting implementation.

The current implementation milestone is the first vertical slice: a Telegram text message becomes a sourced homework card visible in the WebApp.
