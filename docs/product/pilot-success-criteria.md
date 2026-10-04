# StudGroup pilot success criteria

Status: approved pilot framework

Date: 2026-10-03

Final product authority: Ryt1nger

## 1. Purpose

The first product milestone is not completion of every production-grade capability in Module 1. It is evidence that StudGroup reliably turns real Telegram group activity into useful academic cards and that real groups are willing to keep using the product.

The initial external pilot lasts 30 days and targets five unrelated student groups. Internal testing on the owner's group happens before those five groups are invited.

Pilot group creation is closed. A group must be allowlisted by the product owner or present a valid owner-issued pilot authorization code. Student invitations cannot bypass this group-level gate.

## 2. Pilot hypothesis

StudGroup creates enough trustworthy value that students use its cards and notifications, headmen spend less time repeating information, and at least some groups voluntarily renew for 599 RUB per 30 days.

## 3. Critical quality criteria

| Metric | Pilot target | Measurement rule |
| --- | --- | --- |
| Knowingly false confirmed deadlines | 0 | A deadline is counted when StudGroup presents an incorrect date as confirmed despite available contradictory evidence. Clearly marked unknown or conflicting data is not a false confirmation. |
| Important-event detection | At least 95% | Compare detected events with a manually reviewed sample of group messages using the Module 0 event types. |
| Manual correction rate | No more than 5 corrections per 100 published cards | Count headman and deputy corrections that change product meaning, excluding spelling-only edits. |
| Notification execution | More than 99% | A due notification must be delivered or have an explicit terminal Telegram/API error. Suppressed duplicates and quiet-hour deferrals are not failures. |
| Revision safety | 100% | A late AI task must never overwrite a newer message edit or confirmed human correction. |
| Developer intervention | No recurring group-specific repair | Setup assistance is allowed; repeated database or queue repair for ordinary group use fails the criterion. |
| AI cost visibility | 100% of AI calls attributed | Tokens and estimated cost must be traceable to group, task, model, and reasoning level. |

The product owner may change a target after baseline measurement, but the change must be committed here with a reason. Metrics must not be silently relaxed after a failure.

## 4. Activation and usage criteria

| Metric | Pilot target |
| --- | --- |
| Member activation | More than 60% of members in each participating group start the bot or WebApp |
| Useful reach | More than 40% of activated members view cards or receive useful notifications during the pilot |
| Headman value | The headman reports that StudGroup reduces repeated manual communication rather than adding work |
| Data trust | Users can identify the source and status of important information and report incorrect data |

These targets are evaluated per group and across the whole pilot. Averages must not hide a group where the product is unusable.

## 5. Commercial criterion

At least two of the five external pilot groups must voluntarily choose to renew for 599 RUB after the pilot. A complimentary extension, owner-funded payment, or renewal obtained without explaining the price does not count.

Payment infrastructure may be connected after product trust is demonstrated. Before live payment integration, willingness to pay may be recorded as an explicit written commitment from the headman.

## 6. Operational guardrails

During the pilot the team must be able to answer:

- Is the API available?
- Is Telegram ingestion receiving and persisting updates?
- Is the oldest actionable background job within its allowed processing window?
- How many AI tokens and rubles has each group consumed?
- Has a newer revision ever been rejected or overwritten incorrectly?
- Did the product owner receive a deduplicated incident alert and recovery notification for every confirmed critical service outage?

The pilot does not require a large dashboard suite. Logs, health checks, a small alert set, and queryable metrics are sufficient when they answer these questions reliably.

## 7. Pilot evidence package

At the end of the 30 days, prepare:

- the metric table for every participating group;
- false-positive, missed-event, conflict, and late-task examples;
- AI cost per group and per published card;
- headman and student feedback;
- infrastructure incidents and developer interventions;
- renewal decisions at the stated price;
- a product-owner decision to continue, revise, or stop the pilot.

## 8. Non-goals

The pilot is not successful merely because all containers are running, every target architecture item is implemented, or the interface looks complete. It succeeds only when real-group data demonstrates trust, usefulness, operability, and willingness to pay.
