# Module 0: Product domain rules

Status: approved

Date: 2026-10-03

Final product authority: Ryt1nger

## 1. Purpose and authority

This document is the canonical source of truth for the approved product rules of StudGroup Module 0. It defines product behavior that backend, frontend, AI processing, notifications, payments, and tests must follow.

This is a living specification and is updated in place. Git history preserves its revisions. Historical PDFs and product documents are obsolete implementation inputs; design mockups describe visual intent only. An approved product rule may be changed only after confirmation by the product owner and a direct update to this file.

Module 0 defines behavior and domain meaning. Technology choices belong to Module 1 and later architecture decisions.

## 2. Product boundaries

- One StudGroup group corresponds to one Telegram group chat.
- Telegram Topics are used when one group needs several discussion sections.
- In the MVP, a user can have only one active StudGroup membership.
- The product must reduce the headman's workload. It must not send empty digests, routine confirmations, or questions that AI can resolve itself.
- AI may infer and propose information, but it must not silently override a protected manual decision.

## 3. Roles and permissions

### Student

- Reads the cards, schedule, materials, and notifications available to the group.
- Cannot submit shared academic data to the bot through a private chat.
- Messages written by a student in the Telegram group may be used as evidence.
- May submit and manage only their own personal schedule and personal settings.

### Headman

- Manages the group and its critical settings.
- May send academic information directly to the bot in natural language.
- May confirm, edit, and cancel shared academic data.
- Appoints a deputy by sending the deputy's Telegram username to the bot.
- Controls subscription, group deletion, and final conflict decisions.

### Deputy

- The role becomes active only after the invited user accepts it through the bot or deep link.
- Has the same rights as the headman for ordinary academic data.
- Cannot appoint deputies, replace the headman, manage payment, delete the group, or change critical access settings.
- If a conflict with the headman cannot be resolved automatically, the headman makes the final decision.

### Partner and administrator

- A partner sees only their referral links, promo code, accruals, and payouts.
- An administrator is a support role. Administrative access must be audited and is not part of normal group operation.

## 4. Membership and group lifecycle

### Connecting a group

- The headman adds the bot as an administrator with only the required minimum permissions and runs `/connect`.
- The backend verifies that the caller is a Telegram group administrator before creating the StudGroup group and assigning the headman role.
- During the closed pilot, group creation additionally requires owner authorization through an allowlist or pilot code.

### Joining and switching groups

- A student joins through `/start` using a unique one-time invitation code or link generated in a batch by the headman.
- An invitation is bound to one group, expires after 24 hours, may be revoked, and is consumed by the first successful activation.
- The backend verifies actual membership in the Telegram chat before activating access.
- Manual approval by the headman is not required.
- Switching groups requires a warning and explicit confirmation.
- The previous membership becomes `left`, notifications stop, and its history remains subject to retention rules.

### Leaving the Telegram group

- When Telegram membership cannot be confirmed, StudGroup membership becomes `suspended` and access and notifications stop.
- The system checks again after 24 hours and also provides a manual recheck action.
- If the user has returned, membership becomes `active`; otherwise it becomes `left`.

### Deleting a group

- Only the headman may request deletion, and the action requires confirmation.
- The group becomes `deletion_pending`; ingestion, AI processing, notifications, and WebApp mutations stop.
- The group can be restored for seven days.
- After seven days, its educational data is deleted according to the approved deletion workflow.

### Importing existing semester context

- The headman may upload a Telegram Desktop export for the current semester during onboarding.
- The last seven days are analysed completely. Older messages receive a low-cost scan, followed by deep analysis only for still-relevant long-term academic information.
- Long-term information includes control work, tests, credits, exams, projects, presentations, future deadlines, active schedule patterns, permanent schedule changes, and materials connected to future events.
- Only active or future facts become cards. Imported evidence is labelled as imported.
- StudGroup does not request or store the headman's personal Telegram session.
- Raw imported data follows retention from its original message timestamp. Already-expired raw data is deleted after required extraction and validation.

## 5. Personal schedule

- A student may send a screenshot, file, or text containing their personal schedule.
- AI extracts subject, day, start and end time, classroom or link, teacher when available, and recurrence.
- Supported recurrence modes are every week, first/second week, and odd/even week.
- The student must confirm the extracted schedule before it becomes active.
- The source file or text is stored as evidence under the general retention policy.
- A confirmed personal schedule is valid until the end of the current semester. It must be uploaded or reconfirmed for the next semester.
- Without a personal schedule, the student uses the confirmed group schedule or a clearly marked inferred group pattern.

### Week mapping

- If the group already has a reliable week pattern, the bot proposes it to the student.
- Otherwise, the student identifies which schedule is the first and second week, or odd and even week, and confirms the current week.
- A personal correction changes only that student's mapping.

### Priority and conflicts

- A confirmed personal schedule is the default schedule shown to that student.
- A confirmed group change does not silently overwrite a conflicting personal schedule.
- The student chooses either `Apply group change` or `Keep my schedule`.
- A non-urgent explicit conflict may appear in no more than one non-empty personal digest per day.
- A conflict involving a class within the next 12 hours is sent immediately.
- If the student does not respond, both versions remain visible with `needs_clarification`, without repeated spam.
- A date-specific change affects only that date. An explicit permanent statement changes the recurring pattern.

### Inferred group patterns

- AI may infer a normal weekly pattern after two identical cycles.
- An odd/even pattern requires two full alternations, normally about four weeks.
- Before that threshold, the pattern is marked as inferred rather than confirmed.
- A confirmed pattern remains active until a new explicit change appears.

## 6. Source priority and conflict resolution

The descending source priority is:

1. Final headman decision on a conflict.
2. Confirmed manual correction by the headman.
3. Confirmed manual correction by the deputy.
4. Clear direct message from the headman or deputy to the bot.
5. Explicit correction in the group chat.
6. New unambiguous group message.
7. Original message or attachment.
8. Contextual AI inference.

Additional rules:

- A newer explicit correction may replace an older source of the same level.
- Questions, guesses, and jokes are not corrections.
- AI cannot remove a manual field lock. A strong contradiction becomes a human question.
- A later clarification may silently update a non-urgent card, but every change remains in revision history.

## 7. Academic cards

Supported card types:

| Code | Meaning | Typical required data |
| --- | --- | --- |
| `homework` | Homework | subject, content, deadline |
| `control` | Control work | subject, date, topics |
| `test` | Test | subject, date or deadline, format |
| `exam` | Credit or exam | subject, date, format |
| `lesson` | Class | subject, time, classroom or link |
| `reschedule` | Reschedule or cancellation | original event and new condition |
| `announcement` | Important announcement | content and validity period |
| `material` | Academic material | subject, file or link, context |

Common data includes group, type, title, description, event date and time, source, status, urgency, confidence, visibility, revision, and timestamps.

The core lifecycle is:

`detected -> incomplete_hidden -> needs_clarification -> published -> completed | cancelled`

- Every change creates a new revision and records its evidence.
- Missing critical data must remain unknown; AI must not invent it.
- Updating a published card creates a new revision of the same card, not a duplicate card.

## 8. Incomplete and changed information

- An urgent incomplete event is shown with only confirmed data and explicit unknown fields.
- A non-urgent incomplete event remains hidden until the group, headman, deputy, or digest supplies enough information.
- If an incomplete event becomes urgent, the confirmed portion is published with an incomplete status.
- Changes to date, time, cancellation, place, link, or deadline create a new revision and a notification.
- Minor description, topic, or additional-file changes are applied quietly while preserving history.
- A doubtful change is not applied until re-analysis or human clarification succeeds.

## 9. Background processing and AI levels

### Processing cadence

- No new messages means no processing run.
- One to four new messages are processed no later than ten minutes after arrival.
- Five or more new messages are processed every five minutes while the chat remains active.
- Files, media, and clearly urgent information may trigger faster processing.
- A direct message from the headman or deputy is processed separately and immediately.

### DeepSeek reasoning cascade

1. No AI for service events, known duplicates, and technical spam.
2. Minimal reasoning for conversation classification and chatter removal.
3. Stronger reasoning for useful-information extraction and contextual checks.
4. Maximum reasoning for complex conflicts, ambiguous deadlines, and important contradictions.

Tokens, cost, retries, reasoning level, and outcome are recorded per group.

### Confidence

- `85–100%`: apply automatically when source priority and required-field rules allow it.
- `65–84%`: expand context, wait for possible additions, and analyse again.
- `0–64%`: do not publish; ask a human only when the unresolved item is genuinely important.
- Thresholds are configuration values and may be adjusted after analysing pilot errors.
- Confidence never overrides source priority.

### Adaptive waiting

| Event horizon | Maximum wait before re-analysis |
| --- | --- |
| Less than 3 hours | No wait; extended check immediately |
| 3–24 hours | 5 minutes |
| 1–2 days | 15 minutes |
| 3–7 days | 30 minutes |
| More than 7 days or unknown date | 60 minutes |

A new explicit clarification triggers re-analysis before the waiting period ends.

## 10. Chatter, files, and media

- Messages are stored before classification and may receive `non_relevant`.
- `non_relevant` messages skip immediate deep analysis but may be reactivated by a later reply, attachment, clarification, or card relationship.
- Classification may change after re-analysis.
- During the pilot, 2–5% of filtered messages may be sampled for quality control.
- Files and media are normally processed within 5–10 minutes.
- Processing examines the file itself, caption, reply, topic, and surrounding messages.
- Telegram albums are analysed as one collection.
- A random image must not become a homework card without sufficient evidence.

## 11. Human questions and digests

- Before creating a question, AI checks messages, attachments, existing cards, schedules, group profile, history, possible later additions, and performs a second reasoning pass.
- Empty digests and messages saying that a digest is empty are forbidden.
- The headman receives 30–40% of unresolved questions, including final conflicts and administrative decisions.
- The deputy receives 60–70% and the necessary remainder.
- A headman batch contains no more than three or four questions.
- If no deputy exists, the headman receives all genuinely necessary questions.
- There is one primary digest and a second only when required, with a maximum of two non-empty digests per day.
- The headman chooses digest times within the permitted notification window.

## 12. Urgency and notification windows

- Ordinary digests and notifications: `07:00–22:00` in the group timezone.
- Urgent notifications: `07:00–23:00`; between `23:00–07:00` they wait until morning.
- Super-urgent notifications may be delivered at any time.

Super-urgent cases include:

- cancellation or rescheduling of an event starting within 12 hours;
- classroom, link, or format change within 3 hours;
- a new or moved deadline within 3 hours;
- an official emergency message from the headman or deputy.

An ordinary assignment does not become super-urgent only because the system found it late.

If the deputy has not resolved a question, it is escalated to the headman after:

- 15 minutes for an event within 3 hours;
- 30 minutes for an event within 3–12 hours;
- 60 minutes for an event within 12–24 hours;
- 30 minutes when the exact time is unknown.

Non-urgent digest questions return to the next digest instead of immediate escalation. The same question must not be sent twice through different routes.

## 13. Student reminders

| Event | Reminder policy |
| --- | --- |
| Homework or deadline | 24 hours and 2 hours before |
| Control, test, credit, exam | 3 days and 24 hours before |
| Class | Only for cancellation, reschedule, or important change |
| Important announcement | Immediately, subject to the time window |
| Material | No notification unless tied to an urgent event |
| Important change to a near event | Immediately and only once per actual revision |

- The headman selects one IANA timezone for the group.
- Exact timestamps are stored in UTC; calendar meaning and quiet hours use the group timezone.
- A successful notification is unique by user, event, event revision, and notification kind.

## 14. Retention and deletion

| Data | Retention |
| --- | --- |
| Chatter and `non_relevant` messages | 14 days |
| Useful raw text messages | 30 days |
| Local file and media copies | 30 days |
| Cards, revisions, evidence, and attachment metadata | `max(created_at + 90 days, event_date + 7 days)`; without event date, 90 days from creation |

After `delete_at`, the system completely removes the card, revisions, corrections, evidence, references, notification records, and derived AI results associated with that educational data.

Payment and tax records follow their separate legally required retention period.

Telegram username is used only to start deputy appointment. The stable identity is `telegram_user_id`. Audit logs must not duplicate unnecessary personal message content.

## 15. Subscription and payment

### Today feed display rule (owner update, 2026-10-05)

Today sections are ordered: due_today, overdue, new_or_changed, upcoming.
A homework item with a known deadline disappears from every Today section at
exactly 24 hours after its deadline. Earlier-today deadlines already count as
overdue. This is a display rule, not deletion; unknown deadlines and the existing
storage/detail access rules remain unchanged.

- Payment method: SBP through T-Bank.
- Price: `599 RUB` for `30 days` per group.
- The subscription belongs to `group_id`, not to an individual student.
- Access activates only after a verified T-Bank webhook; a success page cannot activate it.
- Repeated provider events must not create a second renewal.
- Early renewal adds 30 days to the current end date; remaining days do not expire.
- After expiration, the group receives two days of full service as a grace period.
- After the grace period, ingestion, AI, and notifications stop, while existing cards remain read-only until normal deletion.
- A confirmed payment restores service without repeating group setup.
- Refund processing adjusts payment, subscription, and partner accrual idempotently.
- The owner will register an individual entrepreneur before production payment connection. Banking, Telegram, receipt, and legal details are verified again in the payment implementation module.

## 16. Referral program

- Partner reward is 15% of the amount actually received after the T-Bank commission.
- Reward applies to the first payment and every later renewal of the attributed group.
- Referral links are perpetual and have no attribution timeout.
- A promo code can be used alone or together with a link.
- The first confirmed source is fixed permanently. A later promo code does not replace an existing referral link attribution.
- A link and promo code belonging to the same partner confirm one attribution and do not double the reward.
- Accrual occurs only after `payment.succeeded`.
- A refund creates a negative adjustment instead of deleting the original ledger item.
- Payouts occur monthly without a minimum amount. Pending and disputed accruals are not paid.
- If a refund occurs after payout, the negative adjustment moves to the next settlement month.

## 17. Technical invariants required by the product

- Mutable aggregates have a revision and use expected-revision checks.
- Telegram updates, AI extraction, notifications, payment webhooks, subscription extensions, and partner accruals use stable idempotency keys.
- A domain mutation and its outbox record are written atomically.
- Retry and redelivery must be safe.
- PostgreSQL is the business-data source of truth; Redis must not contain the only copy of business state.

Minimum aggregates include Group, User, Membership, RawMessage, Attachment, Event, Evidence, Correction, QuestionQueue, NotificationJob, PersonalSchedule, Subscription, Payment, Partner, Referral, Accrual, and AuditLog.

## 18. Critical acceptance scenarios

- [ ] One to four messages start processing within ten minutes; no new messages create no run.
- [ ] Five or more messages are processed every five minutes while active.
- [ ] A later reply can reactivate a previously filtered chatter message.
- [ ] An urgent incomplete event exposes confirmed fields and marks unknown ones.
- [ ] A non-urgent incomplete event remains hidden until resolved or urgent.
- [ ] An unresolved headman/deputy conflict goes to the headman for a final recorded decision.
- [ ] AI investigates before asking; the headman receives at most three or four questions per batch.
- [ ] Empty digests are never sent.
- [ ] A nighttime cancellation for an event in ten hours is delivered immediately as super-urgent.
- [ ] Repeated updates and webhooks do not duplicate cards, notifications, periods, or accruals.
- [ ] Early renewal adds 30 days to the existing end date.
- [ ] After the two-day grace period, new processing stops and existing data is read-only.
- [ ] Referral link A is not replaced by later promo code B.
- [ ] A refund after partner payout creates an adjustment in the next settlement.
- [ ] A personal-schedule screenshot is parsed and confirmed with correct week mapping.
- [ ] A personal/group conflict within 12 hours is delivered immediately; a later conflict waits for the personal digest.
- [ ] A one-off reschedule changes only the specified date.

## 19. Deferred items

The following are not unresolved Module 0 product rules:

- concrete T-Bank SDK and receipt implementation;
- final legal and tax verification before live payments;
- infrastructure and deployment implementation;
- exact AI prompts and provider adapters;
- final frontend component design.

These items may refine implementation but must not silently change the approved behavior above.
