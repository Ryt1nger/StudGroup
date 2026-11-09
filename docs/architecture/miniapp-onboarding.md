# Mini App onboarding

The real first run is scoped to the verified Telegram web session and its active membership. `OnboardingProgress` stores the current step and completion timestamp per membership. The completed-member preview is local UI state and makes no writes.

| Screen and field | Source and write API | Stored state | Validation and reload |
| --- | --- | --- | --- |
| Welcome group and role | `GET /v1/onboarding` after Telegram session bootstrap | `Group`, `Membership`, `OnboardingProgress` | Active membership checked server-side; reloaded on every app opening |
| Personal first and last name | `GET/PATCH /v1/profile` | `UserProfile.first_name`, `last_name` | 60-character server limit; first name required at completion; profile query reloads saved values |
| Contacts Telegram identity | Verified bootstrap session | `User.telegram_user_id`, `WebSession` | Read only; not accepted as a profile form field |
| Contacts email | `GET/PATCH /v1/profile` | `UserProfile.email` | Optional, server-validated, reloaded from profile |
| Subject subgroups | `GET /v1/subgroups`, `PUT /v1/subgroups/{split_id}` | `SubjectSplit`, `Subgroup`, `MemberSubgroup` | Split and option must belong to active group; every split answered before student completion; selected values reloaded from membership |
| SDO/LK credentials | `PATCH /v1/profile` after separate consent | Encrypted `UserProfile` fields | Optional; password write only with approved documents, valid encryption key and current SDO consent; password never returned |
| Terms and data consent | `GET /v1/legal/status`, `POST /v1/legal/consents` | Append-only `LegalConsentEvent` | Separate explicit confirmations, current version and document hash; server verifies both on completion |
| Personalization | Existing `TelegramProvider` and `useDisplay` preferences | Existing local preference cookies | Same controls as profile; changes persist through existing preference mechanism |
| Finish | `POST /v1/onboarding/complete` | `OnboardingProgress.completed_at` | Server checks session, profile, subgroup choices and legal receipts; repeat call is idempotent |

Unpublished legal drafts return `503 legal_not_ready`; new users cannot complete while approval is missing. Completed members keep access. SDO credentials are independent of first-run completion.
