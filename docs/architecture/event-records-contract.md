# Контракт записи «событие» (C+ → Codex / Mini App)

Статус: backend обновлён по решениям владельца 27.10.2026; review и публикация выключены отдельными флагами.
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
`audience_unknown`}. Пусто ⇒ исходный кандидат допускает редакторскую проверку; публичная запись дополнительно требует даты и подтверждения.

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
- Опубликованная запись хранится до даты завершения + 7 дней включительно по времени группы.
  Приватные источники очищаются в исходный срок; подтверждённая структура и история живут независимо.
  Черновики ограничены сроком источника; очистка работает и при отключённой функции.

## Контракт и безопасные ограничения

Пилотный аддитивный контракт: `packages/contracts/image-findings.openapi.yaml`.

- `GET /v1/staff/image-findings/events`: черновики и applied/held источники для редакторов.
- `PUT /v1/staff/image-findings/events/{id}`: явная правка с expected_revision, выбором актуальных
  источников, ручной блокировкой и историей. Роль проверяется повторно и блокируется.
- `GET /v1/group-events` и `/{id}`: только подтверждённые active group-audience записи в своём сроке жизни;
  дата без времени допустима, истечение приватных источников не скрывает запись, при отдельном разрешённом флаге. Секретные linking hints не возвращаются.
- Неизвестная/личная аудитория источника не может быть повышена до общей публикации простым
  изменением payload. Подгруппы пока не публикуются: нужен согласованный способ сопоставления.
- Публикация требует флага `image_event_publication_enabled`, review —
  `image_event_review_enabled`; оба false по умолчанию, дополнительно нужен allowlist группы.
- Текущий frontend не менялся. Правила событий и retention утверждены владельцем; интерфейс и активация остаются отдельным этапом. Разрешение реализации не включает
  разрешение включить эти endpoints для реальных групп.


## Решения владельца 27.10.2026

Отдельные события и публикация только после подтверждения редактором утверждены. API остаётся
выключенным до интеграции frontend и отдельного разрешения на включение. Опубликованная запись
живёт до календарной даты окончания + 7 дней включительно по времени группы (граница удаления —
полночь следующего дня). Известное окончание имеет приоритет; при неизвестном окончании берётся
день начала, кроме подтверждённых многодневных мероприятий без установленного конца — они
требуют уточнения. Дата без времени допустима. Перенос меняет срок после подтверждения.

Источники не продлеваются: исходные сообщения, фотографии, результаты чтений и непринятые
предложения очищаются по прежним правилам. Отдельный подтверждённый snapshot события и его
структурированная история могут пережить источники. API сообщает source_availability=expired для источников; событие не исчезает из-за удаления оригинала. Отмена исключает запись из активного
списка, но сохраняет состояние и согласованный срок жизни. Неизвестные даты не публикуются,
черновики ограничены сроком источника. Полные копии чата не добавляются в payload.

## Calendar and interface consumption (27 October 2026)

The same `/v1/group-events` entity feeds both Events and the existing calendar. Optional `start`
and `end` are inclusive dates in the group's timezone, paired and limited to 366 days. Confirmed
multi-day periods overlap the requested range. `status=active` remains the default;
`status=cancelled|all` exposes retained approved history. `upcoming=true` excludes ended events.
Unknown dates never receive a guessed calendar cell. Responses sort by confirmed start, then ID,
before the existing 200-item cap. `source_url` is unavailable after source expiry/revision change;
`rescheduled` is derived from approved revisions. Neither field creates a second event record.
The main API references this canonical draft contract, and session capabilities expose only
allowlisted flags for the current group and editor role. Reads/manual changes never invoke AI.

The Mini App consumes this contract through the generated shared client. `/events` and its detail
screen are separate from academic deadlines; the existing Today/Schedule calendar renders those
same event IDs and covers multi-day dates. Timestamp ends are exclusive for calendar placement
(an end at midnight does not create a zero-length extra day); date-only ends remain inclusive.
Editor routes retain the original expected revision across background refreshes and require an
explicit reload after conflict. Default-disabled group capabilities hide event navigation/queries.
All event query variants invalidate after manual mutations and refetch on focus/reconnect.
