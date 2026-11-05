# RANEPA SDO integration — read-only discovery

Status: experimental adapter in a feature branch; **not enabled in production**.

## Confirmed user-provided entry points (2026-11-05)

- Student timetable: `https://my.ranepa.ru/lk/student/schedule`.
- LMS calendar of assessments: `https://lms.ranepa.ru/calendar/view.php?view=upcoming`.
- Example course: `https://lms.ranepa.ru/course/view.php?id=89093` (Основы российской государственности).
- The lecture-link list resides **inside individual courses**, not in a single global LMS tab.
  Some courses have no online meeting at all. Several activities can have identical visible titles.

The University also publishes group timetable pages under `https://my.ranepa.ru/schedule/`,
but unattended access and a stable response/API contract are **not verified**. An external
public request may encounter CAPTCHA. Never bypass CAPTCHA or automate a student password login.

## Implemented in this branch

`studgroup.ranepa_lms` is an isolated, read-only *candidate* adapter:

1. Pins the allowed course host/path and rejects credentials, extra URL query parameters,
   unknown hosts, HTTP, fragments, and invalid course IDs.
2. Can request `core_course_get_contents` using a pre-authorized Moodle web-service
   token, if the institution has enabled that function for the user's account.
   The token is supplied at invocation, not read from `UserProfile` or stored in the DB.
3. Reads only visible course URL/webinar/BBB/Zoom activities whose names or section
   suggest lectures or remote attendance. It returns the **Moodle activity URL**,
   not a claimed final conference URL.
4. Deduplicates **by exact activity URL**, not teacher name or displayed title.
5. Fails closed on blocked APIs, redirects, malformed payloads, phishing hosts,
   or hidden/inaccessible activities. Does not create cards, write schedule rows,
   notify students, or contact AI providers.

This is a tested **parsing/probing layer**, not a completed synchronization service.
No new database tables, production credentials, new public endpoints, or UI
changes are introduced by this branch.

## Production integration design (next stages, not implemented)

**Group timetable** — attach each StudGroup group ID to its verified university
group identifier, not a student's private account. Import authoritative occurrences
(date, local time, subject, teacher, location, subgroup, format) in read-only preview
with a source fingerprint. Never overwrite verified headman/manual edits or publish
uncertain changes.

**Course registry** — maintain an explicit per-group mapping
`(studgroup_group_id, subject_id, lms_course_id)`, verified by an authorized
group staff member. Reject cross-group course sharing by default. Never infer
an identical course merely because its teacher or abbreviated title matches.

**Meeting links** — import activity candidates on a schedule; map to a lesson
only when subject, audience/subgroup, date/time, and type are unambiguous.
An activity page may lead to an external webinar after student authentication.
Without that verification the UI must say **Open in SDO**, never **Join now**.
When course activities share a title but have different URLs, retain all as
separate candidates. A course with zero links must produce zero joining buttons.

**Calendar & assessments** — read authorized calendar event data from the Moodle
service if available. Represent the exact source deadline/timezone; classify
a control point only when the source explicitly identifies it as such. Do not
turn coursework events into assignments without confirming their types.

**Personal results** — BRS scores and attendance require user-specific,
permission-scoped access and must never be distributed through group snapshots.
The existing profile's write-only encrypted SDO password field is **not**
permission to run a headless password login or share credentials.

## Preconditions before live synchronization

1. Confirm the institution-supported authorization/API or export route for
   `my.ranepa.ru` and `lms.ranepa.ru` with a permitted account.
2. Obtain one **sanitized** sample timetable response and one course-activity response.
   Remove cookies, access tokens, session IDs, and personally identifying data.
3. Resolve the exact RANEPA group identifier for БИ 1.2 and register course-to-subject
   mappings with staff confirmation.
4. Add versioned API contracts, isolated group-scoped storage, sync cursor,
   backoff/circuit-breaker, dry-run comparison, audit, and review before enabling
   any live schedule or online-link changes.
5. Preserve the current source-priority and schedule-version behavior, including
   headman overrides and subgroup boundaries.

This work uses no paid AI calls and has no production rollout path by default.

## Smoke tests

```bash
cd apps/backend
pytest -q tests/test_ranepa_lms.py
```
