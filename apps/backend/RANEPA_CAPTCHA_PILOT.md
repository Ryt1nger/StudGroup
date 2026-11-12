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
  persists browser cookies after a verified crawl. Do not copy those cookies
  between accounts or server IPs.

## Opt-in setup for one account (only after code review)

1. Add GitHub Actions **secret** `RUCAPTCHA_API_KEY` to the StudGroup repository.
   This secret is mapped to the Pydantic setting `CAPTCHA_API_KEY` inside the
   workflow. Never post its value to an issue, chat or logs.
2. Add GitHub Actions **variable** `RANEPA_CAPTCHA_PILOT_ENABLED=true`.
   The workflow enables paid CAPTCHA solving **only for a manual**
   `workflow_dispatch` with explicit `pilot_group_id` and `pilot_user_id`.
   Scheduled multi-account runs never enable it.
3. Verify existing encrypted YDB credential, legal approval and consent for
   the single selected pilot account, and a minimum RuCaptcha test balance.
4. From the default branch, dispatch `.github/workflows/ranepa-crawl.yml`
   manually with the two IDs. Do **not** dispatch repeatedly while a challenge
   or balance error is unresolved.
5. Review job result and permitted aggregate diagnostics. The job writes no
   CAPTCHA screenshot, secret or provider response to an artifact. If the
   CAPTCHA fails, investigate the actual sanitized DOM/slider geometry before
   changing any production settings.

The Pydantic defaults are
`RANEPA_CAPTCHA_ENABLED=false`,
`RANEPA_CAPTCHA_TIMEOUT_SECONDS=110` and
`RANEPA_CAPTCHA_MAX_TASKS_PER_RUN=1`. A missing key produces a warning,
not a startup failure. The quota is **per worker process/workflow run**, not
a global billing limit across all possible manual dispatches; avoid repeated
runs and control the RuCaptcha account balance independently.

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
