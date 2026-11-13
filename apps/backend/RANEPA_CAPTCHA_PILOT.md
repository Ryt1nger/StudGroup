# RANEPA StormWall CAPTCHA: controlled RuCaptcha pilot

## Status

Optional adapter in PR #147; **disabled by default, not proven against a live StormWall
challenge and not deployed**. The current server screenshot shows a two-slider
picture challenge on `my.ranepa.ru`, not Moodle. The legacy RuCaptcha Coordinates
API is a possible fit, but it returns *image coordinates*, not a guaranteed StormWall
solution. A static screenshot may be insufficient to infer how the moving halves
should align; do not assume a high solve rate from synthetic tests.

## Worker behaviour

- Playwright performs the ordinary StormWall JavaScript redirect in the same
  browser context. Its short `get_jhash`/`__js_p_` spinner is classified as
  `challenge_pending` and given a bounded wait.
- An actual CAPTCHA is recognised via the observed Russian text or dedicated
  `Captcha` page title. It is never parsed as a timetable.
- The optional adapter requires two visible range controls, or precisely two
  elements with the observed duplicate `id="myRange"`. It captures only the
  challenge region from the `my.ranepa.ru` host, below the provider's image
  size limit. No profile, cookies, HAR, passwords or page HTML are uploaded.
- One provider `coordinates` request returns exactly two points, in upper/lower
  order. Both points and slider positions are checked **before either one-shot
  drag**. The original schedule reader verifies the outcome and student group;
  coordinates alone never count as a successful sign-in.
- Invalid coordinates, different HTML, provider error, timeout or no API key
  preserve the existing safe `captcha` failure. No aggressive retry loop.
- Fernet-encrypted YDB `storage_state` from the parent session-reuse PR
  persists browser cookies after a verified crawl, **only when**
  `RANEPA_SESSION_REUSE_ENABLED=true`. Scheduled jobs leave this feature off.
  Do not copy those cookies between accounts or server IPs.

## Manual pilot retry versus scheduled refresh

The scheduled multi-account queue retains the normal 24-hour refresh interval.
When an operator manually dispatches `ranepa-crawl.yml` with both
`pilot_group_id` and `pilot_user_id`, that one approved account can requeue
its previously completed (succeeded or failed) current-week job immediately.
This is specifically for repeatable single-account tests without a 24-hour wait:
other users, queued work and actively running jobs are not reset. It does not
automatically retry CAPTCHAs: each manually dispatched run still has at most
one paid task under the existing budget. Do not repeatedly dispatch on errors.

## Opt-in setup for one account (only after code review)

1. Add GitHub Actions **secret** `RUCAPTCHA_API_KEY` to the StudGroup repository.
   This secret is mapped to the Pydantic setting `CAPTCHA_API_KEY` inside the
   workflow. Never post its value to an issue, chat or logs.
2. Add GitHub Actions **variables** `RANEPA_CAPTCHA_PILOT_ENABLED=true` and
   `RANEPA_SESSION_REUSE_PILOT_ENABLED=true`. The first enables the paid
   solver; the second enables encrypted cookie-state restoration and updates.
   Both features stay **off by default** and are gated independently.
   The workflow enables paid CAPTCHA solving **only for a manual**
   `workflow_dispatch` with explicit `pilot_group_id` and `pilot_user_id`.
   Scheduled multi-account runs never enable it.
3. Verify existing encrypted YDB credential, legal approval and consent for
   the single selected pilot account, and a minimum RuCaptcha test balance.
4. From the default branch, dispatch `.github/workflows/ranepa-crawl.yml`
   manually with the two IDs. Start with `diagnostic_only=true` (the default,
   **free** mode). Proceed to a paid run only after reviewing the stage codes.
   Do **not** dispatch repeatedly while a challenge or balance error is unresolved.
5. Review job result and permitted aggregate diagnostics. The job writes no
   CAPTCHA screenshot, secret or provider response to an artifact. If the
   CAPTCHA fails, investigate the actual sanitized DOM/slider geometry before
   changing any production settings.

The Pydantic defaults are
`RANEPA_SESSION_REUSE_ENABLED=false`,
`RANEPA_CAPTCHA_ENABLED=false`,
`RANEPA_CAPTCHA_TIMEOUT_SECONDS=110` and
`RANEPA_CAPTCHA_MAX_TASKS_PER_RUN=1`. A missing key produces a warning,
not a startup failure. The quota is **per worker process/workflow run**, not
a global billing limit across all possible manual dispatches; avoid repeated
runs and control the RuCaptcha account balance independently.

## Two-step manual diagnostic (no payment first)

The manual **RANEPA paced crawl** workflow now has a
`diagnostic_only` boolean checkbox, checked by default.

**First run — diagnostic only (checked):**
- It still targets exactly the selected `pilot_group_id` and `pilot_user_id`.
- On an interactive StormWall CAPTCHA, Playwright inspects only the LK
  widget, checks two controls, and captures the clipped challenge **locally**.
- It does **not** call RuCaptcha, move sliders, write a screenshot artifact,
  or spend the paid task quota. The pilot is expected to end with
  `RANEPA_PILOT_RESULT failed` when the interactive gate is present;
  this is not a test failure for the diagnostic mode.
- Search the job log for **`RANEPA_CAPTCHA_STAGE`** and check the last stage.

**Second run — one paid attempt (uncheck `diagnostic_only`):**
- Only proceed when the dry-run reported `controls_confirmed`,
  `capture_ready`, and `diagnostic_complete_no_provider_call`.
- It will call RuCaptcha once at most; no automatic retry.
- It logs only fixed stage names, never URLs, student data, API keys,
  images, provider response bodies, token values or raw coordinates.

Selected stage codes and what they mean:

| Stage | Interpretation |
| --- | --- |
| `challenge_detected_month` | The crawler encountered a CAPTCHA on the LK page |
| `instruction_not_visible` | The expected slider instructions were not visible |
| `control_count_mismatch` | Neither the range input nor `myRange` selector found exactly two controls |
| `control_bounds_invalid` / `control_order_invalid` | The observed geometry is unsafe or unsupported |
| `controls_confirmed` | The expected two visible controls passed geometry checks |
| `capture_ready` | Local crop was captured; this does **not** mean a paid task occurred |
| `diagnostic_complete_no_provider_call` | Offline inspection finished with **zero RuCaptcha calls** |
| `provider_request_started` | A paid task call was attempted; billing must be checked in RuCaptcha |
| `provider_answer_received` | RuCaptcha returned a response (acceptance not proven) |
| `provider_answer_invalid` | Answer did not contain exactly two expected coordinates |
| `coordinates_outside_controls` | Coordinates do not map to both slider rows |
| `drag_submitted` | Slider motions were performed; **not** proof that the gate passed |
| `challenge_still_present_month` | The subsequent LK check still reported CAPTCHA |
| `month_verified_after_challenge` | The normal timetable/identity checks succeeded |
| `pilot_finished_captcha` | The final worker status is still CAPTCHA |
| `provider_timeout` / `provider_error` | Upstream provider failed; exception details are not logged |

When a stage is absent, **do not infer that its action happened**.
An unchanged account and a `captcha` result do not prove an API request.
Keep sensitive diagnostics in the private operator workflow; do not enable
verbose browser/network tracing or upload HAR/cookies to artifacts.

## Validation checklist

- [x] Official `2captcha-python` SDK configured for `rucaptcha.com`; uv lock updated.
- [x] Offline test doubles for provider responses, Playwright drags, origin and
  bounds validation, malformed data, missing key and worker retry limits.
- [x] Separate JS challenge, CAPTCHA, login and verified timetable statuses.
- [ ] Real provider credentials and paid API interaction.
- [ ] Verification of StormWall's exact control geometry and successful
  navigation with an authorized **single-account** pilot.
- [ ] Measured reliability and costs before increasing batch quotas or enabling
  unattended production use.
