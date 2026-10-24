# Subject subgroups

## Product behavior

The headman configures every subject that is taught in separate subgroups in the private Telegram
cabinet. For each subject they enter 2–6 subgroups and free-form recognition signals such as a
teacher's surname, a group number, or a room. The naming model turns those signals into short labels;
the headman reviews and may edit every label before saving and can rename it later.

After a split is added, existing students receive a private bot prompt. A newly invited student is
asked immediately after joining. The choice is per subject and can be changed in the bot or through
the Mini App membership API.

## Card routing

The configured subgroup names and signals are included in the trusted subject catalog supplied to
the extraction model. The backend then independently verifies the model's audience label against
the configured names/signals and the cited message text. Surnames are matched with common Russian
case endings; explicit forms such as `гр. 2` and `вторая подгруппа` are supported. A bare exercise
number is not treated as a subgroup number.

- A recognised card stores `subgroup_id` and is returned only to members who chose that subgroup.
- A student who has not answered sees all subgroup cards for that subject.
- Headmen and deputies always see all cards.
- If the subject is split but no unique subgroup can be established, the card remains common to the
  group and enters the existing review queue.
- Removing a subject split clears subgroup targeting from retained cards, making them common again.

Filtering is enforced in backend list/detail/Today/subject/notification reads. Client rendering is
not a security or correctness boundary.

## Storage

- `subject_splits`: one row per `(group, subject_id)`.
- `subgroups`: ordered labels and JSON recognition signals for a split.
- `member_subgroups`: one selected subgroup per `(membership, split)`; absence means unanswered.
- `homework.subgroup_id` and `academic_deadlines.subgroup_id`: nullable card audience.

The API change is additive and documented in contract 0.18.0. The Telegram setup flow remains the
authoritative administrative interface; no public API lets a student create or alter group-wide
subgroup definitions.

## Owner test command

`/test_subgroups` opens a read-only click-through of both the headman setup and student selection
journeys. It is accepted only from `OWNER_TELEGRAM_USER_ID` or `BOT_ADMIN_USER_ID`; other users see
an access notice. The demo uses fixed example subjects and never writes subgroup definitions,
member choices, cards, or notifications.
