# Contract compatibility

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
- Production CORS allows only the configured Telegram WebApp origin. It allows
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
