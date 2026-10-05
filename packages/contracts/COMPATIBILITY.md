# Contract compatibility

## Today deadline window (0.2.2)

Backend now orders due_today before overdue. Any known deadline at least 24 hours
in the past is excluded from Today even if the card was recently created/updated.
Overdue means deadline strictly before server time (including earlier today).
Unknown deadlines do not expire by this rule. Clients must preserve server order;
the detail endpoint and existing retention rules are unchanged.

## Next lesson (0.2.1)

TodayResponse.next_lesson is an additive optional field; the backend supplies it
as null or {state: current|upcoming, lesson: LessonOccurrence}. Selection uses
server time and group timezone. Current means starts_at <= server_time < ends_at;
otherwise the earliest future lesson within the next 32 days is selected. Missing
week anchor never yields a guessed alternating lesson. Existing empty describes
homework sections only; next_lesson can be present when empty=true.

## Schedule integration (0.2.0)

`GET /v1/schedule?start=YYYY-MM-DD&end=YYYY-MM-DD` returns an inclusive range
of at most 32 days. Occurrences are ordered by starts_at and id. Week alternation
uses first_week_anchor, never ISO calendar parity. Missing anchor omits alternating
patterns and sets week_state=needs_clarification; the client must show this state.
selected_week refers to start. All timestamps include timezone offset.
Frontend regenerates the client for the new schedule.read permission and error enum.

## Versioning

- HTTP paths use the major prefix `/v1`.
- Adding an optional response field or a new endpoint is backward-compatible.
- Removing or renaming a field, changing its meaning, making an optional field
  required, or removing an enum value is breaking.
- Adding an enum value is treated as potentially breaking for generated clients
  and requires a changelog entry plus frontend coordination.
- Breaking changes require product-owner approval and a new major API prefix.

## Session token

- `POST /v1/session/bootstrap` accepts the raw Telegram Mini App `initData` in
  the `init_data` field. The backend validates its signature, bot identity and
  `auth_date`; data older than 10 minutes is rejected.
- The response contains 32 cryptographically random bytes encoded as unpadded
  base64url: exactly 43 characters matching `^[A-Za-z0-9_-]{43}$`. It is not a
  JWT and carries no client-readable claims.
- Only a SHA-256 hash of the token is stored by the backend.
- The token expires 12 hours after issue. Slice 1 has no refresh token: reopening
  the Mini App performs bootstrap again with fresh Telegram `initData`.
- The frontend sends `Authorization: Bearer <access_token>` and keeps the token
  in memory only. It must not write it to localStorage, IndexedDB, URL parameters,
  analytics, logs or error reports.
- Production CORS allows only the configured origin that actually serves the
  deployed WebApp (for the pilot, its Cloudflare Pages origin). Telegram itself
  is not added as a CORS origin. The policy allows
  `GET`, `POST`, `PUT` and the `Authorization`/`Content-Type` headers; browser
  credentials are disabled. Explicit local-development origins are configuration,
  never a production wildcard.
- Invitation expiry/consumption and a group rejected by the closed-pilot gate are
  handled in the bot flow. Session bootstrap does not return those errors: a user
  without an activated membership receives `access_state=no_active_group`.

## Concurrency

Every mutable homework representation has a monotonically increasing `revision`.
The completion mutation requires `expected_revision`. A stale write returns HTTP
409 with error code `revision_conflict`; the client refetches the resource, shows
the updated state and only then lets the user repeat the action.

The personal completion write does not increment the group-card `revision` and
does not alter its group `status`. `expected_revision` protects the user from
marking an obsolete card revision; repeating the same boolean value is idempotent.

## Today response size

Slice 1 returns the complete bounded Today feed without pagination. Backend rules
already limit `upcoming` to three cards and cards are de-duplicated across the four
sections. Pagination can be added only with an explicit section-merge contract.

## Telegram Mini App deep link

- Bot links use `startapp=hw_<homework-id-without-hyphens>`.
- `start_param` therefore matches `^hw_[0-9a-f]{32}$` and is 35 characters.
- The parameter is an untrusted navigation hint, not a credential or signature.
  The frontend restores the UUID hyphens, opens the detail route and lets the
  backend enforce session, membership and resource ownership.
- Invalid, unknown, inaccessible and expired identifiers resolve to the normal
  validation, 404, 403 and 410 states; they never bypass the Today gate.

## UI semantics fixed by the owner

- `urgency` is the only source of urgent styling. The frontend must not infer
  urgency from a deadline.
- `verification_state=manual_confirmed` is the only state labelled
  `Подтверждено`; `from_group_message` is labelled `Из сообщения группы`.
- The personal action is labelled `Выполнено мной`.
- Numeric confidence and revision values are never displayed to students.
- Source availability is a normal state. `unavailable` is not rendered as a
  generic application error.
- `source.action.url` is backend-built and, in Slice 1, may use only an HTTPS URL
  on the exact `t.me` host. Redirector and arbitrary external hosts are rejected.
