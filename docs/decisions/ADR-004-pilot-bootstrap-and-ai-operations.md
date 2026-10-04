# ADR-004: Pilot bootstrap and AI operations

Status: accepted

Date: 2026-10-04

## Context

Implementation needs stable decisions for Telegram onboarding, existing group history, frontend scope, AI provider boundaries, outage handling, and pilot access. Fully designing Modules 2–15 before coding would delay the first validated product journey.

The Telegram Bot API does not let a bot retrieve arbitrary history from before it joined the group. Telegram documents `messages.getHistory` as a user-only method. Membership lookup for other users is guaranteed through `getChatMember` when the bot is a chat administrator.

## Options

- Fully specify every remaining module before implementation.
- Start coding with implicit assumptions and decide behavior inside implementation.
- Approve only cross-module blocking decisions, preserve explicit boundaries, and defer non-blocking detail.

## Decision

Approve the eight decisions in `docs/product/mvp-implementation-boundaries.md` and begin the first vertical slice.

Use Telegram Bot API only for continuous group operation. Bootstrap prior context through an explicit Telegram Desktop history import, not through the headman's personal Telegram session.

Use DeepSeek behind an internal provider boundary. Persist source data before AI execution, retry failures safely, and notify the product owner immediately through a private system-administrator bot mode when a provider incident is confirmed.

Keep the pilot closed through a configurable owner-controlled group allowlist or pilot authorization code.

## Consequences

- Development begins without fourteen additional detailed module-design phases.
- Existing groups can recover relevant semester context without handling a user's Telegram credentials.
- The bot requires minimal administrator status in every connected group.
- Backend schema planning covers all approved screens while API implementation remains incremental.
- DeepSeek failure delays extraction but does not lose Telegram data.
- The product owner receives operational alerts without exposing technical incidents to ordinary users.
- Deferred choices remain explicit and cannot silently become product rules through code.

## References

- [Telegram Bot API: getChatMember](https://core.telegram.org/bots/api#getchatmember)
- [Telegram API: messages.getHistory](https://core.telegram.org/method/messages.getHistory)
