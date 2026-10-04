# Claude frontend handoff: Stage 0 and Vertical Slice 1

Status: ready for frontend review

Date: 2026-10-04

Product: StudGroup Telegram Mini App

Frontend owner: Claude

Final product and UX authority: Ryt1nger

Backend, contracts, infrastructure, and technical coordination: Codex

Visual design owner: ChatGPT designer

## 1. Your role

You own the Telegram Mini App frontend in `apps/frontend`:

- application structure and frontend stack proposal;
- faithful implementation of approved designs;
- mobile-first layout and Telegram WebView behavior;
- light and dark themes;
- accessibility and responsive behavior;
- client-side request, cache, loading, empty, offline, partial, and error states;
- integration with the approved OpenAPI contract and generated TypeScript client;
- frontend unit, component, and critical browser tests.

You do not own backend behavior, authentication decisions, domain rules, payment rules, or API semantics. If required data is missing, submit a contract requirement rather than inventing a private frontend model.

The product owner makes the final decision on product scope, priority, copy, and acceptance.

## 2. Read these files before proposing code

Read the following repository documents in order:

1. `AGENTS.md`
2. `CLAUDE.md`
3. `docs/product/module-0-domain-rules.md`
4. `docs/product/module-1-technical-foundation.md`
5. `docs/product/mvp-implementation-boundaries.md`
6. `docs/product/screen-data-matrix.md`
7. `docs/architecture/pilot-vertical-slice.md`
8. `docs/product/pilot-success-criteria.md`
9. `docs/workflow/team-workflow.md`
10. `packages/contracts/README.md`

Priority when sources disagree:

1. Latest explicit product-owner decision committed in the canonical Markdown specifications.
2. Approved OpenAPI contract.
3. This handoff.
4. Design mockups and assets.
5. Earlier PDFs, archive documents, and chat summaries.

Mockups describe visual intent. They do not override approved roles, statuses, permissions, retention, or data behavior.

## 3. Product summary

StudGroup observes one Telegram student-group chat and turns relevant conversation, files, schedule changes, assignments, deadlines, and announcements into structured academic data.

Core principles:

- one StudGroup group maps to one Telegram group chat;
- Telegram Topics remain inside that group;
- a user has only one active group in the MVP;
- AI works in the background and must not invent missing facts;
- every important card exposes its status and evidence/source;
- students cannot change group academic truth through private bot messages;
- the headman and deputy manage academic corrections under the approved role rules;
- a confirmed personal schedule later takes priority for its student, with explicit conflict handling;
- the product must reduce headman workload and never send empty operational noise.

The first slice is not the whole product. Backend data design accounts for every approved screen, while implementation remains incremental.

## 4. Vertical Slice 1 outcome

The required end-to-end journey is:

```text
Owner authorizes a pilot group
        ↓
Headman adds the bot and runs /connect
        ↓
Student activates a one-time invitation
        ↓
A real Telegram text message describes homework
        ↓
Backend stores, analyses, and creates a sourced Event
        ↓
The Today screen shows the homework card
        ↓
The student opens homework details and its source
```

The system must run online without the product owner's laptop.

For the first frontend delivery, only the Today and homework-detail data paths are live. Other approved navigation destinations may be present as polished unavailable states, not fake data-connected features.

## 5. Screens in the first frontend delivery

### Live screens

1. Mini App bootstrap and authenticated loading.
2. Today.
3. Homework detail.
4. Source action or source-unavailable explanation.

### Required service states

- loading;
- empty Today feed;
- partial/incomplete homework;
- `needs_clarification`;
- permission denied;
- invitation expired or consumed;
- user is not a current Telegram group member;
- user has no active group;
- group not authorized for the closed pilot;
- backend temporarily unavailable;
- AI processing pending;
- offline with retry;
- stale content with visible freshness;
- unexpected error with a safe correlation identifier when provided.

### Prepared navigation sections

- Today;
- Tasks;
- Schedule;
- Subjects.

The prepared sections must look intentional and clearly state that their live data functionality is not enabled yet. Do not populate them with misleading hard-coded academic data in production builds.

## 6. Today screen data requirements

The Today view must be able to render:

- current group and local date;
- current user's role and allowed actions;
- card identifier, type, title, subject summary, and short description;
- exact deadline when known;
- explicit unknown deadline when not known;
- status, urgency, visibility, confidence band, and revision;
- source kind, source availability, and source action metadata;
- created and updated timestamps;
- processing or clarification state;
- empty and partial responses;
- freshness metadata.

The frontend must not derive authorization from the role label alone. Backend-provided permissions are authoritative.

## 7. Homework detail data requirements

The homework-detail view must support:

- full description;
- subject;
- deadline or explicit unknown state;
- status and confidence band;
- current revision;
- evidence/source summary;
- source action;
- related attachments when introduced later;
- revision/history summary when introduced in Slice 2;
- personal completion state labelled “completed for me”, not group completion;
- report-error action when its backend flow is introduced;
- permissions for headman/deputy actions when introduced.

Fields that do not exist in Slice 1 must be optional in shared types and must not be fabricated by frontend code.

## 8. Telegram Mini App integration rules

- Load Telegram's official Mini App JavaScript bridge before application code.
- Wrap `window.Telegram.WebApp` behind one small frontend adapter. Components must not access the global object directly.
- Send the raw `Telegram.WebApp.initData` to the backend authentication endpoint.
- Never authenticate or authorize using `initDataUnsafe`.
- Never place the bot token or backend signing secret in frontend code.
- Use Telegram theme and viewport information through the adapter, while preserving the approved StudGroup visual identity.
- Support ordinary-browser development with an explicit mock Telegram adapter that cannot be enabled accidentally in production.
- Respect Telegram back-button and viewport behavior where relevant.

Telegram's official documentation states that `initData` must be validated by the backend and that `initDataUnsafe` must not be trusted: <https://core.telegram.org/bots/webapps>.

## 9. Recommended frontend stack for review

This is the baseline proposal, not permission to replace it silently. Confirm it with the product owner and Codex during Stage 0.

### Runtime

- React;
- TypeScript with strict mode;
- Vite;
- React Router;
- TanStack Query for server state, retries, cache, and request lifecycle;
- Zod only at untrusted runtime boundaries where generated OpenAPI types are insufficient;
- `clsx` for conditional class composition;
- CSS custom properties plus CSS Modules for design tokens and component styling;
- native `Intl` APIs for presentation formatting where possible.

### API contract and generation

- OpenAPI from `packages/contracts/openapi.yaml` is authoritative;
- generated TypeScript types and client live in `packages/shared-types` or the agreed generated package;
- `@hey-api/openapi-ts` is the initial generator candidate;
- generated files are never edited manually;
- contract generation and drift checks run in CI;
- handwritten duplicate request/response interfaces are forbidden.

### Testing

- Vitest;
- React Testing Library;
- `@testing-library/user-event`;
- `@testing-library/jest-dom`;
- Mock Service Worker for API-boundary tests;
- Playwright for critical Telegram WebApp browser flows.

### Development quality

- ESLint;
- Prettier;
- TypeScript strict checks;
- production build verification in CI.

### Not included initially

- Redux or another global client-state store without demonstrated need;
- a second private API model;
- a large component framework that prevents faithful mockup implementation;
- animation libraries before core interaction and low-performance WebView behavior are proven;
- direct DeepSeek or Telegram Bot API calls from the frontend.

Exact package versions must be current stable releases compatible with one another and must be pinned by the committed lockfile. Do not use floating versions in CI.

## 10. Styling and design-system expectations

After the product owner supplies mockups and assets:

- inventory every screen, light/dark pair, icon, logo, illustration, font, and state;
- identify duplicates, inconsistent active navigation, conflicting copy, missing states, and assets that cannot be used directly;
- extract design tokens for color, spacing, radius, typography, shadow, border, motion, and safe-area spacing;
- prefer reusable semantic components over page-specific copies;
- preserve visual quality while meeting contrast, touch-target, text-scaling, and reduced-motion requirements;
- support long Russian subject names, teacher names, and assignment titles;
- do not encode text inside raster assets when it should remain selectable UI text;
- provide graceful behavior when optional illustrations are absent;
- ensure dark and light versions have identical information architecture.

The design package will be supplied separately by the product owner after this brief.

## 11. Expected component boundaries

Propose final names during Stage 0, but plan around these responsibilities:

- `TelegramAppProvider` and Telegram adapter;
- `AppBootstrap` and session boundary;
- router and route-level error boundary;
- `AppShell` and bottom navigation;
- theme and design-token layer;
- Today feed;
- academic-card primitives;
- homework card and homework detail;
- status, urgency, confidence, and source badges;
- source action;
- loading skeletons;
- empty, offline, permission, and service-error states;
- generated API client provider;
- query-key and cache policy module.

Keep domain wording aligned with the contract. Do not collapse distinct states merely because the mockup uses the same color.

## 12. Stage 0 deliverables from Claude

Before feature implementation, return the following:

1. Frontend stack proposal with reasons and any changes to the baseline above.
2. Package/dependency list grouped into runtime and development dependencies.
3. Route map for Slice 1 and prepared sections.
4. Component tree and ownership boundaries.
5. Design-token plan derived from the supplied assets.
6. Asset inventory with missing or unusable items.
7. Screen/state matrix covering every supplied mockup.
8. Contract review listing every field required from backend for Today and homework detail.
9. Telegram adapter design, including ordinary-browser mock behavior.
10. Test plan for loading, empty, offline, permission, source, and happy-path states.
11. Risks, blocking questions, and a clear recommendation.

Write the resulting frontend architecture proposal to `apps/frontend/FRONTEND_ARCHITECTURE.md`. Put asset and screen findings in `apps/frontend/DESIGN_HANDOFF.md`.

Do not implement a conflicting API type while waiting for backend changes. Record required contract changes explicitly for Codex review.

## 13. Contract workflow with Codex

1. Claude reviews the mockups and this brief.
2. Claude returns exact screen data requirements and state requirements.
3. Codex drafts the minimal OpenAPI Slice 1 contract with examples and errors.
4. Claude reviews the contract for renderability and missing states.
5. The product owner approves behavior and visible copy.
6. Codex commits the approved contract and generated client/types.
7. Backend and frontend implementation proceed in parallel.
8. Contract tests and the integrated end-to-end scenario run before acceptance.

Contract changes require compatibility notes. Neither frontend nor backend may silently redefine a shared enum or field.

## 14. Acceptance standard for the frontend part of Slice 1

The frontend portion is accepted when:

- it runs inside Telegram and in the explicit local mock environment;
- authentication uses backend-validated `initData`;
- Today renders a real backend-created homework card;
- homework detail and source behavior match the contract;
- light and dark themes preserve the same information;
- loading, empty, incomplete, offline, permission, and backend-error states are usable;
- navigation does not falsely imply unfinished sections are live;
- no secrets or handwritten duplicate API models exist;
- unit/component tests pass;
- the critical Playwright journey passes against the agreed test backend;
- the product owner accepts the result on a mobile Telegram client.

## 15. First response expected from Claude

After receiving this brief plus all mockups and assets, do not begin with broad implementation. First respond with:

- confirmation that the canonical documents were read;
- a concise design and asset audit;
- the recommended frontend stack and dependency list;
- the component and route plan;
- the exact backend fields needed for the live screens;
- conflicts or missing states that require product-owner decisions;
- the recommended order of frontend work.

The goal is a fast Stage 0 agreement followed by implementation, not another long product-design phase.
