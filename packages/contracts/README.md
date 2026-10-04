# API contracts

This directory is the shared boundary between backend and frontend.

Current contents:

- [`openapi.yaml`](openapi.yaml) — the reviewed HTTP boundary for Slice 1;
- [`COMPATIBILITY.md`](COMPATIBILITY.md) — compatibility and token rules;
- [`CHANGELOG.md`](CHANGELOG.md) — contract changes.

The contract intentionally exposes only implemented Slice 1 behavior. Telegram
invitation flows remain bot-only, and prepared WebApp sections do not receive
placeholder endpoints.

Contract-first workflow:

1. Propose the contract.
2. Review product meaning and compatibility.
3. Approve the contract.
4. Implement backend and frontend independently.
5. Run contract and integration tests.
