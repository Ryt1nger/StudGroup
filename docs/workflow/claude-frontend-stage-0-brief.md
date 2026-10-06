# Claude frontend handoff: Stage 0 and Vertical Slice 1

Status: implementation authorized by the owner on 2026-10-05

Date: 2026-10-05

## Coordinated frontend repair: route transitions

At the owner's explicit request, Codex added subtle 180 ms content entrance in
AppShell. A follow-up repair removes native View Transition snapshots because
they can flash the glass navigation panel. Do not reintroduce viewTransition
props on route links or browser root snapshots. Background/navigation stay live.
BottomNav keeps both active/inactive images mounted with stable sources and
switches opacity over 120 ms instead of replacing image URLs. Content animation
does not transform fixed descendants. Reduced-motion disables both animations.
Preserve these changes and tests/navigationStability.test.tsx when synchronizing
cloud frontend work. Real Telegram-device animation checks remain pending.
Owner follow-up: preserve shared --sg-nav-height / --sg-nav-bottom and the
16px --sg-action-nav-gap. CompletionBar uses these tokens instead of a hardcoded
76px bottom offset; BottomNav has the matching fixed height. Verify the visible
gap in both themes and on Telegram devices when extending these screens.

## Current assignment: start frontend implementation

### Stage 2 handoff: connect Tasks to real API (contract 0.4.0)

Regenerate the client from openapi.yaml. Replace HomeworkListDemo and the
handwritten GET /homework call with generated listHomework/HomeworkListResponse.
Enable /tasks outside mock mode. Pass filter=all|today|week|mine|archive to the server.
Use cursor pagination with limit=50 and a Load more action; merge pages by id,
then group once into upcoming/unknown/done/overdue/cancelled in the owner-approved
order. Do not treat a single empty page as a globally empty result.
Server filters are authoritative: group the returned items without applying the
selected date/completion filter a second time on the client.
Reset cursors on filter/session/group changes and on completion mutations/conflicts; refetch
Today/details as before. Server selection time is frozen per cursor chain;
use generated_at for the live clock, never device wall time.

Archive contains immediately cancelled and passed-deadline items. Active filters
and Today exclude them; date-only deadlines stay active until next group day.
There is no +12h cancellation or +24h overdue display window anymore. Preserve current
Today rules, icon mapping, 0.90 dark background veil, stable navigation images,
content-only transitions and the 16px completion-action gap.
Mocks must implement the canonical response, filters and pagination. Add tests
for multiple pages, duplicated ids, cancellation boundary, mutation refresh,
filter reset and cursor expiry (422 invalid_request: restart list once, no loop).
Show loading/retry/no-access/offline/empty and loading-more failure without losing
already fetched cards. Run lint/types/unit/build/Playwright; do not claim live
backend verification until backend and PostgreSQL are actually running.

DeepSeek remains backend-only. This handoff adds no frontend AI credentials or
requests, no mock data in production and no new visual redesign.

Owner update: hide all personally completed cards in Today, including updated
ones; omit empty sections/headings. Preserve the mock filter and tests added by
Codex. Full Tasks screen is now requested as a mock-mode implementation first;
completed and older overdue items remain available there, not sourced from Today.

Owner update 2026-10-05, contract 0.4.0: add Archive to Tasks. Cancelled and
passed-deadline items are archived immediately and excluded from Today/active
Tasks. Personal completion and retention remain unchanged. Preserve the updated
mock filters, archive helper and tests during synchronization.

Schedule is included in Slice 1 by the owner's 2026-10-05 decision. Contract
0.2.1 includes GET /v1/schedule with start/end query dates and schedule.read permission.
Regenerate the client, use the existing schedule mockups, and render the explicit
week_state=needs_clarification when alternation has no anchor. The endpoint returns
an inclusive date range (maximum 32 days), lesson timestamps with offset and
backend ordering. Cancel/reschedule overrides remain outside this increment.

Backend integration update (2026-10-05):

- Regenerate the client from contract 0.2.1. Schedule populated, empty and
  needs-clarification examples are now included; SessionActive includes schedule.read.
- Connect NextClassCard to TodayResponse.next_lesson. It is null or
  {state: current | upcoming, lesson: LessonOccurrence}. Backend selects it using
  server time and group timezone, looking from today through the next 31 days.
- The Today empty flag describes homework sections only. Do not hide a non-null
  next_lesson just because empty is true. Omitted/null next_lesson hides the card.
- Choose the initial schedule request week using session.server_time and group
  timezone, not Date.now(). Advance server time by elapsed monotonic time where
  needed. Add a test with an intentionally incorrect device date.
- Current lesson data does not expose lesson type, subject ID, or actual last
  content update time. Do not invent values or present generated_at as a content
  update timestamp. These remain explicitly absent until a documented increment.
- Rerun frontend unit and Playwright tests after regenerating the client. Mock
  tests are not a substitute for the real-backend acceptance run.

The owner has authorized development. Use the existing majority of mockups and
assets in `design/` and your current `apps/frontend/DESIGN_HANDOFF.md` and
`FRONTEND_ARCHITECTURE.md`. Begin work immediately using those materials.

Deliver a runnable frontend: scaffold the approved stack, Telegram adapter,
theme tokens, variable Inter, app shell and navigation; generate the API client
from `packages/contracts/openapi.yaml` (0.2.1). Implement Today, homework detail,
source and personal completion using the available designs. Develop against the
contract examples with development-only mocks until the backend is available.

Cover loading, empty, offline, access and expired-session states. Session expiry
requires reopening the Mini App from Telegram; refresh is excluded from Slice 1.
Use available components for routine states; list genuinely missing designs
without delaying the runnable portions. Do not introduce new product features.

Keep undefined decorative badges hidden until their semantics are settled. The
backend owns urgency, permissions and feed ordering. Unimplemented sections show
`Раздел готовится`. No AI chat, payment or schedule implementation in this slice.

Acceptance: the app starts locally; generated types drive the requests; a mocked
homework opens details and source; completion and its failure are handled; both
themes work. Run lint, type checks, tests and production build. Report the run
command, changed files, screenshots and remaining integration gaps. Update the
existing documents in place. Own frontend workspace configuration and client
generation; coordinate any contract change with Codex. Backend and infra remain
Codex's responsibility.

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

Priority when current sources disagree:

1. Latest explicit product-owner decision committed in the canonical Markdown specifications.
2. Approved OpenAPI contract.
3. This handoff.
4. Design mockups and assets.

Mockups describe visual intent. They do not override approved roles, statuses, permissions, retention, or data behavior.

Do not open, compare, reconcile, cite, or preserve `StudGroup documentation v0.3`, earlier PDFs, archive documents, or historical chat summaries as product specifications. They are obsolete. In particular, the old price of 499 RUB is invalid; the current approved price is 599 RUB per 30 days per group.

Maintain one current frontend architecture file and one current design handoff file. Apply approved changes directly to them. Do not create `v2`, `updated`, `final`, or similar parallel copies; Git history preserves revisions.

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

Evaluate supplied mockups against current canonical Markdown rules only. Do not use historical PDFs to “complete” or correct the current design package.

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
