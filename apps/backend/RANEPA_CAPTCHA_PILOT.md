# RANEPA StormWall CAPTCHA: controlled RuCaptcha pilot

## Status

Optional adapter introduced in PR #147; **disabled by default, and not
proven to pass a live StormWall challenge**. Manual one-account pilots have
received provider answers but have not passed the CAPTCHA. The native-thumb CSS preflight fails closed when a safe first click cannot be verified.
The current server screenshot shows a two-slider
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
| `diagnostic_capture_grid_verified` | Offline PNG pixel dimensions match the expected Playwright clip dimensions; **not** proof of a valid solution |
| `diagnostic_capture_grid_mismatch` | Offline PNG pixel grid does not match the expected clip, or bytes cannot be decoded; do not infer a coordinate transform |
| `diagnostic_range_state_at_min` | Both live HTML range values were at their declared minimum; this is **not** a measurement of thumb pixel locations |
| `diagnostic_range_state_not_at_min` | At least one live HTML range value was above minimum; hard-coded left-start assumptions are unverified |
| `diagnostic_range_state_unavailable` | Exactly two supported inputs could not be read or validated; do not guess their initial positions |
| `diagnostic_geometry_rect_consistent` | Read-only DOM rectangles match Playwright boxes, with standard horizontal orientation; **thumb location still unknown** |
| `diagnostic_geometry_custom_range` | DOM rectangles match, but at least one range input uses custom appearance; thumb location still unknown |
| `diagnostic_geometry_nonstandard` | CSS direction, writing mode, transform or pointer events are not compatible with the simple horizontal assumptions |
| `diagnostic_geometry_rect_mismatch` | Browser DOM rectangle and Playwright bounding box differ; do not rely on hard-coded geometry |
| `diagnostic_geometry_unavailable` | DOM or style information could not be read safely; no geometric conclusion |
| `tracks_marked_for_provider` | Both slider tracks were outlined on the in-memory copy for RuCaptcha |
| `capture_annotation_failed` | The PNG dimensions or encoding could not be safely matched; no provider task was sent |
| `diagnostic_complete_no_provider_call` | Offline inspection finished with **zero RuCaptcha calls** |
| `provider_request_started` | A paid task call was attempted; billing must be checked in RuCaptcha |
| `provider_answer_received` | RuCaptcha returned a response (acceptance not proven) |
| `provider_answer_invalid` | Answer did not contain exactly two expected coordinates |
| `coordinates_outside_controls` | Coordinates do not map to both slider rows |
| `target_1_x_outside_track` / `target_2_x_outside_track` | Provider click falls horizontally outside the corresponding slider track |
| `target_1_above_track` / `target_2_above_track` | Provider click falls above the corresponding slider track, possibly on the circle |
| `target_1_below_track` / `target_2_below_track` | Provider click falls below the corresponding slider track |
| `drag_submitted` | Slider motions were performed; **not** proof that the gate passed |
| `challenge_still_present_month` | The subsequent LK check still reported CAPTCHA |
| `month_verified_after_challenge` | The normal timetable/identity checks succeeded |
| `pilot_finished_captcha` | The final worker status is still CAPTCHA |
| `provider_timeout` / `provider_error` | Upstream provider failed; exception details are not logged |

For the 2026-11-13 paid run #43419540993, the provider returned two
well-formed points, but at least one was not on the expected slider track.
The old response was deliberately not logged, so the exact mismatch cannot be
reconstructed. The instruction sent to RuCaptcha explicitly requests two
clicks on the **tracks**, not on the circular puzzle. As the 2026-11-13
follow-up #43422527111 still returned `target_1_above_track`, the provider
image is now marked **in memory** with a green frame for the top track and
blue frame for the lower track, without rescaling or changing the DOM.
Only this cropped, marked image is sent to RuCaptcha; the original circle,
coordinates and slider geometry remain unchanged. The diagnostic distinguishes
off-track X, above-track Y and below-track Y without exposing any raw values. **Do not automatically clamp or ignore Y:** the
challenge permits only one movement and the worker's answer may refer to
another part of the image. Stop after a rejected response instead of retrying
paid tasks blindly.

## Offline geometry audit following run #43443988974

The 2026-11-13 manually dispatched `main` run
[#43443988974](https://github.com/Ryt1nger/StudGroup/actions/runs/43443988974)
confirmed `tracks_marked_for_provider`, `provider_answer_received`, then
`target_1_above_track`, `coordinates_outside_controls`, and
`motion_not_submitted`. The local validator prevented both drags. Neither a
successful CAPTCHA submission nor a server-side rejection was observed.

The free `diagnostic_only` path now checks whether the locally captured PNG
pixel dimensions match the Playwright CSS clip. It logs **only** a fixed
`diagnostic_capture_grid_verified` or `diagnostic_capture_grid_mismatch` stage,
without image contents, coordinates, API requests, annotations or mouse motion.
A matching grid rules out a simple image-size discrepancy, but does **not**
establish that RuCaptcha's two selected points correspond to valid slider
positions. Earlier paid runs discarded the raw positions intentionally; the
precise source of `target_1_above_track` cannot be reconstructed from logs.

The current implementation still assumes a drag starting point of
`box["x"] + 25` without observing the actual slider-thumb position. This
assumption is **not validated by the offline tests** and may be wrong for
different CSS/range configurations. Do not relax coordinate checks, infer
targets from clicks on the circle, increase paid attempts, or enable a
production rollout on the basis of these diagnostics.

Only the existing authorized LK and SDO workflow is in scope;
investigate unsupported slider geometry before considering further paid
attempts.

When a stage is absent, **do not infer that its action happened**.
An unchanged account and a `captcha` result do not prove an API request.
Keep sensitive diagnostics in the private operator workflow; do not enable
verbose browser/network tracing or upload HAR/cookies to artifacts.

## Offline audit: coordinate contract and initial slider state

**Scope:** only the manually approved pilot's `diagnostic_only=true` path.
The paid path and its accepted-coordinate/movement logic are unchanged.

1. **Provider contract.** RuCaptcha's Coordinates method returns ordered
   points in the submitted image, not mouse-drags or verified slider values.
   The legacy SDK parser requires exactly two unsigned integer coordinate
   pairs. The adapter translates from clipped-image to viewport coordinates,
   validates each point against its matching input's bounds, and rejects
   out-of-track answers before any mouse action. The provider's `code` is not
   recorded. See https://rucaptcha.com/api-docs/coordinates.
2. **Screenshot grid.** Run #43456938782 on 2026-11-13 reported
   `diagnostic_capture_grid_verified`; this rules out a simple mismatch of
   PNG dimensions versus the screenshot clip in that run. It does not prove
   that workers clicked the intended control or that a real range thumb
   starts where the adapter expects.
3. **Initial values.** A new offline-only read of two HTML
   `input[type="range"]` elements inspects `type`, `min`, `max`, live
   `value`, and `disabled`. It emits **only one fixed stage**:
   `diagnostic_range_state_at_min`,
   `diagnostic_range_state_not_at_min`, or
   `diagnostic_range_state_unavailable`. No raw values, DOM snippets,
   screenshots or session data are logged or exported.
4. **Starting pixel coordinates remain unverified.** Paid-path motion still
   starts at `box["x"] + 25` for each slider without verifying actual CSS
   thumb center. Even `diagnostic_range_state_at_min` cannot validate that
   pixel location: CSS/native input appearance, styling, thumb dimensions,
   browser and rendering details can change the visual handle position.
5. **Sufficiency of a single static image remains unknown.** The provider
   receives one frozen screenshot; the repository has no approved sample
   showing that the required two end positions can be inferred from that
   frame without interactive feedback. No challenge screenshots or raw
   provider responses were retained from the paid attempts, so those
   attempts cannot establish why the first point fell above the upper
   control. Do not claim compatibility or infer target locations from
   incomplete evidence.

### Decision and safe next steps

- Do not launch another paid RuCaptcha attempt merely because the PNG
  dimensions match, and do not automatically clamp coordinates or increase
  retries. Keep the manual pilot limits and production defaults.
- Treat read-only range semantics as an **audit signal**, not a replacement
  for measuring visual thumb geometry or a solution for a CAPTCHA.
- Keep the existing LK and SDO integration as the **only** collection path.
  Respect the approved account scope, and never expose authentication
  tokens or captured challenges to public artifacts.
- Revisit live CAPTCHA automation only after a verified interface contract
  and successful security review. Offline synthetic tests do not demonstrate
  successful passage of StormWall.

## Read-only DOM geometry audit (LK/SDO only)

The next free manual pilot `diagnostic_only=true` inspects the two
`input[type="range"]` elements on the already encountered LK StormWall
challenge. It reads each input's `getBoundingClientRect()` and limited
computed CSS properties (`direction`, `writingMode`, `transform`,
`pointerEvents`, `appearance`) and compares rectangles with Playwright's
previous bounding boxes. **This does not dispatch mouse/keyboard events,
perform any drag, call RuCaptcha, write screenshots to artifacts, or reveal
styles/coordinates in logs.** Exactly one constant stage is logged; the
challenge is not treated as a timetable.

A consistent rectangle and horizontal CSS orientation establish only
the **input element's box**, not the browser-rendered slider thumb. The
existing paid-path assumption `box["x"] + 25` remains unverified.
Browsers do not expose a robust, universal DOM rectangle for native or
CSS-styled range thumbs. We deliberately do not derive a start point,
modify paid-path motion, relax bounds, or claim that a single still image
can solve this particular challenge. Any nonstandard style, rectangle
mismatch or unreadable DOM should block assumptions, not trigger another
paid try.

This audit supports **only the existing RANEPA LK and SDO integration**.
No alternate schedule sources, external timetable feeds, or substitute
collection paths are proposed.

## Current checkpoint: post-drag verification (2026-11-14)

**Observed paid run:** [#43566210710](https://github.com/Ryt1nger/StudGroup/actions/runs/43566210710)
used `diagnostic_only=false`, one provider task, and reported
`provider_answer_received`, `coordinates_validated`,
`drag_submitted`, and `challenge_still_present_month`.
The page was checked ~23 ms after the already completed 800 ms post-drag
pause. This does **not** prove that an AJAX request existed, that the
slider thumbs moved, or that the server explicitly rejected an answer.

**This checkpoint changes only observation and the bounded wait, not how the
two drags or the single paid request are performed.** After both mouse drags,
the adapter tries to read the two live native range values again, compares
them with the values observed before the drags, and logs exactly one fixed
stage:

- `drag_values_both_changed`: both native input values changed;
- `drag_values_one_changed`: exactly one changed;
- `drag_values_unchanged`: both readable values stayed the same;
- `drag_values_unavailable`: native inputs were absent, changed identity,
  unreadable, or incompatible with the read-only check.

**These values are not coordinates of the visual thumbs.** For a custom slider,
DOM values might not change even if something moves visually. No numeric
values, screenshots, DOM HTML, challenge payloads, or secrets are logged.

The LK page is then observed for **up to six seconds**, without clicking,
replaying a drag, calling the provider, or accepting CAPTCHA success. The
wait emits one fixed state (examples):

- `challenge_page_changed_unverified`: page looks rendered; normal LK
  group-identity/month verification is still required;
- `challenge_still_visible_after_wait`: the CAPTCHA remained visible for
  the bounded wait;
- `challenge_unresolved_after_wait`: only transient or empty states were
  observed during the window;
- `challenge_login_visible_after_drag`,
  `challenge_redirect_after_drag`, or `challenge_state_unavailable`:
  an intermediate state, not successful access.

The normal `read_month` / `read_week` pipeline is still the **only**
authority for accepting a schedule, after verifying the academic group.
For a failed pilot:

1. **Values unchanged:** inspect whether the current hard-coded
   `box["x"] + 25` grabbed the intended control. Do not blindly alter
   coordinates or spend more provider tasks.
2. **Values changed, challenge persists:** confirm the challenge interface
   and whether the provider's coordinate answer is actually compatible
   with the two-slider task. No repeat paid attempts based only on this log.
3. **Page changed but month verification fails:** investigate the
   already-existing LK identity/session reader, not the CAPTCHA provider.
4. **Month and week verified:** proceed to the existing SDO links/courses
   path and validate persistence. This is the only end-to-end success.

**Anti-loop gate:** the next meaningful pilot must answer a question that
the previous one could not answer. This PR alone does not establish
StormWall acceptance or justify further paid tests. Scope stays restricted
to the existing RANEPA LK and SDO workflow; no alternative timetable
sources.

## One-shot StormWall slider: observed DOM and measured preflight (2026-11-14)

The **authorized user's Chrome DevTools screenshots** establish that StormWall
uses two **native CSS-styled** `input[type="range"]` elements with
classes `slider-top` and `slider-bottom`, duplicate id `myRange`,
`min="0"`, `max="360"`, `step="0.5"`, initial `value="0"`, and
input rectangles of **300 × 30 CSS pixels**. The visual white pill-shaped
thumb is NOT a separate `div` or `span`. The earlier PR code requiring an
explicit handle element was incompatible with this observed markup.

The screenshots also demonstrate **one-shot lockout**: after an attempted
movement, a control may gain `disabled` and the `disable-sl` class
**while its range value is back at zero**. The image element can retain a
`transform: rotate(...)` when this occurs. Therefore an unchanged post-release
input value is not reliable proof that no movement occurred; it may have
changed during the drag and reset after release.

### What the guarded adapter now does

1. **Before RuCaptcha is billed**, inspect the browser's same-origin,
   accessible CSSOM for a matching `::-webkit-slider-thumb` rule with
   explicit, unambiguous numeric `width` and `height` in CSS pixels.
   `getComputedStyle(input, "::-webkit-slider-thumb")` is *not* used:
   Chromium can return the entire input's 300 × 30 size for that query.
2. Verify each input's enabled, initial/minimum state, horizontal CSS
   orientation, pointer hit target, input rectangle and plausible thumb
   dimensions (16–85 px width). Calculate each first-click point from the
   **measured input rectangle and CSS thumb width**. No fixed `x + 25`,
   fallback guess, probe click or synthetic `value` modification.
3. Recheck both geometry and active state after provider latency, then only
   the *pending* handle before its sole press and after a hover. Do not
   require the first handle to remain enabled after its own release.
   Reject zero-distance targets.
4. During each **single continuous drag**, inspect input value and image
   rotation **before mouse-up**, without any additional click. After the
   sole release inspect `disabled`/`disable-sl`. Emit fixed stages such as
   `drag_2_live_value_changed`, `drag_2_live_image_changed`,
   `drag_2_live_unchanged`, `drag_2_release_locked` or
   `drag_2_release_enabled`. Never log values, angles, coordinates or
   session data. Original read-only post-drag and bounded LK verification
   remain unchanged.
5. If native thumb CSS is inaccessible or ambiguous, report
   `thumb_css_unverified_no_provider_no_motion` **before the paid call**.
   The **free** `diagnostic_only=true` path emits
   `diagnostic_native_thumb_css_verified` or
   `diagnostic_native_thumb_css_unavailable` without mouse input,
   third-party requests or screenshot artifacts.

### Anti-loop decision

- **First**, full CI must pass. Then run one **free**, single-account
  diagnostic on `main` to validate that the authorized LK challenge really
  exposes readable native thumb CSS. A simulated CSSOM test is not evidence
  of the production widget.
- If CSS verification is unavailable, **do not spend any RuCaptcha balance**.
  Inspect the minimal CSS selector and width/height from the normal Chrome
  Styles view. Never hard-code a screen coordinate.
- If CSS verification succeeds, exactly one manually authorized pilot may
  check live motion, lockout, challenge state, LK group identity, and
  month/week schedule. Avoid repeated attempts on a single-use control.
- A `drag_*_release_locked` stage means the one-shot interaction was
  consumed. **It does not mean the CAPTCHA was accepted**. Only the original
  LK month/week verification (then SDO links) can establish useful success.

No alternate timetable sources, cookies, authentication tokens or extra
provider tasks are introduced.

## Proposed change after the failed Coordinates pilot (2026-11-14)

The authorized paid run [#43578254900](https://github.com/Ryt1nger/StudGroup/actions/runs/43578254900)
verified both native-thumb starts but returned
`target_1_above_track` / `coordinates_outside_controls` /
`motion_not_submitted`. The legacy RuCaptcha `Coordinates` task asks a
worker to **click image points**; the returned points do not express the
rotation angles needed for the two-half-circle challenge. Re-running that
format is not justified.

RuCaptcha's **API v1** documents `method=rotatecaptcha` with one or
several images and an angle for **each** image in one task (for example,
`40|200|-120` for three images). The Python SDK exposes
`AsyncTwoCaptcha.rotate(files_dict, angle=..., hintText=...)`; with two
temporary PNG files it submits a **single task** containing `file_1`
and `file_2`. RuCaptcha's newer `RotateTask` API v2, in contrast,
documents one image and one `solution.rotate` per task.

The paid RANEPA pilot now uses `solve_rotating_halves`, **not** the old
`solve_slider_captcha` Coordinates method (which remains only for the
read-only diagnostic path and legacy unit coverage). It:

1. Confirms the exact LK host, two enabled native ranges and CSS-derived
   starting thumb centers, plus two original `data:image/*;base64` assets
   from the CAPTCHA's `img-left` and `img-right` elements.
2. Converts only those isolated image assets to 200×200-ish PNG files in
   an automatically removed temporary directory. It does **not** upload
   the account page, credentials, network traffic, cookies or profile.
3. Requests exactly **one** RuCaptcha v1 `rotatecaptcha` task with both
   images. Uses `angle=5` rather than the default 40° step and
   decodes exactly two signed whole-degree answers. Ambiguous,
   out-of-range, zero-distance or unreadable cases abort before clicks.
4. Maps clockwise angle `a % 360` to a target position across each
   measured native thumb's usable travel. Rechecks initial geometry
   after provider latency and on hover, then executes **at most one**
   continuous press-drag-release per thumb.
5. Reads value/image rotation before mouse-up and
   `disabled`/`disable-sl` after mouse-up. If the first movement
   produces no observable change, it aborts instead of consuming the
   second one-shot input.
6. Still requires the original LK month/week and academic-group checks,
   then the existing SDO path. A `rotate_drags_submitted_verification_pending`
   stage **does not** mean the challenge passed.

**Free preflight:** In `diagnostic_only=true`, the worker now adds
`diagnostic_rotate_halves_available` or
`diagnostic_rotate_halves_unavailable` after the original free control/CSS
diagnostics. This checks that both original data-URI images are readable,
decode safely and can be converted to two PNGs **before** any paid run.
It does not transmit those images, touch a slider, or create an artifact.
Do not start a paid pilot unless **both**
`diagnostic_native_thumb_css_verified` and
`diagnostic_rotate_halves_available` appear on the current `main` SHA.

**Limitations and go/no-go:** A provider's ability to return two angles
does not establish that a human can correctly orient two **masked half
images** or that the angles correspond exactly to the LK slider values.
The association is a testable hypothesis supported by observed
`max=360` inputs and the rotation style on `img-right`, not a
confirmed live solution. Run full CI, then a **free** diagnostic on the
deployed revision. The first paid pilot must be separately authorized
and limited to one account and one provider task. Do not repeat it if
angle results fail to solve StormWall; inspect only fixed stage codes.

Official API documentation:
- https://rucaptcha.com/api-rucaptcha (RotateCaptcha API v1)
- https://rucaptcha.com/api-docs/rotate (RotateTask API v2)

## Paid RotateCaptcha request failed before an answer (2026-11-14)

Manual pilot [#43590610809](https://github.com/Ryt1nger/StudGroup/actions/runs/43590610809)
on `main` SHA `7e36370` successfully verified the two original image
halves (`rotate_two_halves_validated_read_only`), then emitted
`rotate_provider_request_started`, `rotate_provider_failed`,
and `motion_not_submitted` **within one second**. No angles were
received, no slider was pressed, and there is no evidence of CAPTCHA
acceptance. The old `target_1_above_track` error was not encountered.

**Important:** the original exception was deliberately hidden from logs,
so the server/API/SDK failure class and charging outcome are **unknown**.
Never claim the provider rejected a particular parameter or that a task
was billed without evidence. Do **not** repeat this exact paid run.

Follow-up changes:
- The two-image RuCaptcha `AsyncTwoCaptcha.rotate(files_dict)` behavior
  is checked using the real pinned `2captcha-python 2.1.2` SDK with a
  mocked API client; no network calls or billing. Assert single
  `rotatecaptcha` submission, exactly two file fields, correctly renamed
  `textinstructions`, and strict two-angle response.
- Keep the worker hint short and ASCII-only, within **140 UTF-8 bytes**,
  to avoid encoding/byte-limit ambiguity in the legacy multipart API.
- Provider failures now produce only a fixed, safe category such as
  `rotate_provider_api_bad_parameters`,
  `rotate_provider_api_upload_rotate_failed`,
  `rotate_provider_api_zero_balance`,
  `rotate_provider_network`, `rotate_provider_sdk_validation`,
  or `rotate_provider_api_other`. **Never** log raw API responses,
  tokens, paths, upload data, credentials, or exception messages.
  The existing `rotate_provider_failed` stage remains for compatibility.

**Next gate:** full CI must pass and this change must be merged to
`main`. Diagnose the provider failure category on the next separately
approved single-account test before any wider action. A failure in the
new provider stage requires reviewing that specific cause, not an
automatic retry. Keep `RANEPA_CAPTCHA_MAX_TASKS_PER_RUN=1`.

## SDK exception namespace audit (2026-11-14)

The earlier paid run [#43592478380](https://github.com/Ryt1nger/StudGroup/actions/runs/43592478380)
reported `rotate_provider_unknown_error` immediately after
`rotate_provider_request_started`. Auditing the pinned `2captcha-python
2.1.2` shows two distinct exception namespaces: the HTTP client
`twocaptcha.async_api.AsyncApiClient` raises
`twocaptcha.exceptions.api.ApiException/NetworkException`, while
`AsyncTwoCaptcha` may separately raise
`twocaptcha.exceptions.solver.ApiException/NetworkException`.
The previous classifier recognized only the latter, making a real
HTTP-level provider refusal look like an unclassified exception.

The classifier now recognizes **both** namespaces, with offline regression
tests for actual SDK exception classes and redacted fixed-stage logging.
This repairs **diagnostics**, not the as-yet-unknown cause of provider
rejection. No raw exception text, provider payload, key, image, or
session data is logged. Do not infer an API error code or billing outcome
from earlier runs.

A separate SDK-source review confirmed that
`AsyncTwoCaptcha.rotate({"left": path1, "right": path2})` takes the
values of this dict and calls `extract_files()`, which renames them
to **`file_1` and `file_2`** before reaching the multipart HTTP client.
Thus changing `left/right` to `file_1/file_2` in the high-level call
would not change the actual upload. The existing real-SDK offline test
checks those exact form fields.

## Validation checklist

- [x] Official `2captcha-python` SDK configured for `rucaptcha.com`; uv lock updated.
- [x] Offline test doubles for provider responses, Playwright drags, origin and
  bounds validation, malformed data, missing key and worker retry limits.
- [x] Separate JS challenge, CAPTCHA, login and verified timetable statuses.
- [x] A paid provider interaction and response in an explicitly authorized one-account manual pilot; CAPTCHA success **not** established.
- [ ] Verification of StormWall's exact control geometry and successful
  navigation with an authorized **single-account** pilot.
- [ ] Measured reliability and costs before increasing batch quotas or enabling
  unattended production use.
