# Контракт записи «событие» (C+ → Codex / Mini App)

Статус: предложение backend/AI-core C+, задача №04B. Студентам события **не публикуются**.
Ничего в студенческом API, таблицах и семи `semantic_kind` не меняется.

## Откуда берутся события

- Источник данных — `ShadowEvaluationRun.validation_result_json` → `candidates`
  (после `stored.effective_validation`), категория `event`.
- Готовая проекция: `studgroup.analysis.candidates.event_records(validation) -> list[EventRecord]`
  (`contracts.EventRecord`, `StrictDTO`). Повторный анализ чата не нужен: запись самодостаточна.
- Событие приходит в том же ответе Pass 2 (`candidates`), третьего AI-вызова нет. Если модель
  назвала событие неподдерживаемым `semantic_kind`, оно спасается из отклонённого предложения
  (`origin=rejected_proposal`).

## Форма `EventRecord` (JSON)

| поле | тип | смысл |
|---|---|---|
| `record_type` | `"event"` | константа |
| `fingerprint` | str(64) | sha256 от `casefold(title или description)` + `when_text`; стабилен между запусками и источниками, ключ дедупликации |
| `title`, `description` | str | очищенный текст модели |
| `when_text` | str\|null | исходная формулировка даты/времени, **без** вычисленной даты |
| `classification_reason` | str\|null | почему это событие, а не учебное задание |
| `audience` | `{scope: group\|subgroup\|personal\|unknown, label}` | аудитория |
| `confidence` | int 0..100 | |
| `source_message_ids` | list[int] | Telegram id подтверждённых источников |
| `evidence` | list[EvidenceRef] | `telegram_message_id`, `revision`, `fact_paths` |
| `source_status` | `verified`\|`partially_verified`\|`defaulted_to_target` | см. ниже |
| `origin` | `model_candidate`\|`rejected_proposal` | |
| `published` | `false` | всегда |
| `promotion_blockers` | list[str] | почему запись пока нельзя показывать |

`source_status`: `verified` — все процитированные сообщения есть в запросе в нужной ревизии;
`partially_verified` — часть источников отброшена (остались только подтверждённые);
`defaulted_to_target` — ни одной подтверждённой ссылки, привязка к целевому сообщению
**как заведомо недостоверная** (также `hold_code=unverified_source`,
`attention_reasons` содержит `unverified_source`, исходные id в `claimed_source_ids`).
Такая запись никогда не подменяется «надёжным» источником без флага.

`promotion_blockers` ⊆ {`unverified_source`, `title_missing`, `audience_personal`,
`audience_unknown`}. Пусто ⇒ запись пригодна к показу, когда появится API.

## Что нужно от Codex (минимально)

1. Решить место хранения: **(a)** читать события из хранимых результатов (хранение 30 дней,
   `delete_at`) — годится только для превью владельца; **(b)** для Mini App — отдельная таблица
   `group_events` (`group_id`, `fingerprint` UNIQUE на группу, поля `EventRecord`, `state`,
   `source_run_id`, `created_at`, `delete_at`) и миграция. Рекомендуется (b); C+ заполняет её
   только по записям с пустыми `promotion_blockers` и `source_status=verified`.
2. Описать в OpenAPI `GET /v1/groups/{id}/events` (поля как выше, без `fingerprint`,
   `published`, `promotion_blockers`); аудитория фильтруется так же, как у дедлайнов.
3. Подтвердить: событие — не `AcademicDeadline` и не `Homework`; `destination` расширять не нужно.
4. Договориться о жизненном цикле (устаревание по `when_text`, отмена, правка) — C+ эти
   правила не придумывает.

До согласования пунктов 1–4 события остаются только в превью владельца (`held_candidates`).
