# Контракт записи «событие» (C+ → Codex / Mini App)

Статус: backend реализован 26.10.2026; review и публикация выключены отдельными флагами.
Существующий `EventRecord` и семь академических `semantic_kind` совместимы. Событие не является ДЗ или учебным дедлайном.

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

## Реализованный backend

- `group_events` — отдельный черновик/активное/отменённое событие, ревизия, ручная блокировка,
  payload с названием, исходной формулировкой даты, timestamp, местом, описанием, аудиторией и условиями.
- `group_event_sources` — каждое подтверждённое сообщение/ревизия и image job, включая удержанное
  изменение модели. `group_event_revisions` — история принятых изменений. Миграция `0042`.
- `EventRecord` преобразуется в черновик без повторного AI-вызова. Если даты или названия нет,
  исходный кандидат сохраняется; отсутствие названия не ломает пакет. Поля не выдумываются.
- Разные фотографии анализируются независимо. Совпадение title+when_text использует ту же
  fingerprint-семантику, что существующий кандидат. Источники дополняются, повторы не создают
  повторных записей. Обновления/отмена требуют точной идентичности и ревизии события.
- Модель автоматически меняет только не заблокированный черновик. Изменение опубликованного
  или вручную исправленного события сохраняется как `held` finding для старосты.
- Срок хранения временно ограничен исходным 30-дневным окном сообщения. Очистка удаляет событие,
  источники и историю; периодическая очистка работает и при отключённой функции.

## Контракт и безопасные ограничения

Пилотный аддитивный контракт: `packages/contracts/image-findings.openapi.yaml`.

- `GET /v1/staff/image-findings/events`: черновики и applied/held источники для редакторов.
- `PUT /v1/staff/image-findings/events/{id}`: явная правка с expected_revision, выбором актуальных
  источников, ручной блокировкой и историей. Роль проверяется повторно и блокируется.
- `GET /v1/group-events` и `/{id}`: только active, будущие, group-audience события с актуальными
  источниками, при отдельном разрешённом флаге. Секретные linking hints не возвращаются.
- Неизвестная/личная аудитория источника не может быть повышена до общей публикации простым
  изменением payload. Подгруппы пока не публикуются: нужен согласованный способ сопоставления.
- Публикация требует флага `image_event_publication_enabled`, review —
  `image_event_review_enabled`; оба false по умолчанию, дополнительно нужен allowlist группы.
- Текущий frontend не менялся. Утверждение публичной схемы, retention, аудитории и UI находится
  в `OWNER_DECISIONS_REQUIRED` итогового integration report. Разрешение реализации не включает
  разрешение включить эти endpoints для реальных групп.
