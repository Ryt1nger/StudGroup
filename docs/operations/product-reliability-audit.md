# Аудит надёжности StudGroup и план восстановления

Дата среза: 20 ноября 2026 года, Europe/Moscow. Обновление реализации: 21 ноября.

Статус: канонический реестр подтверждённых проблем, фактов и критериев приёмки. Это не заявление,
что проблемы уже устранены. Исполняемый план и текущий checkpoint находятся в
`docs/workflow/product-reliability-execution-prompt.md`.

## Итоговая оценка

Текущее production-состояние: **около 4/10**. Система работает частично, но содержит stale/lost
jobs, длинный backlog, задержки API до 11 секунд, неустойчивые SDO-ссылки, неинформативные
исторические уведомления и зависимость критических workers от внешних расписаний/спящего web.

Оценка 8/10 допустима только после закрытия всех P0, полного CI/PostgreSQL gate, readiness,
устойчивого canary и отсутствия stale leases. 9/10 требует семидневного SLO-окна и независимого
worker. 10/10 — 30-дневного SLO, restore/DR/security/capacity drills.

## Production-факты

### Развёртывание и база

- Render backend `studgroup-api-v2` был `live` на commit `88532ed`; актуальный GitHub main
  21 ноября — `87d047c`, на три commits впереди production.
- Backend `autoDeploy=no`, HTTP health-check path не настроен, отдельного Render worker нет.
- PostgreSQL — Supabase US West, `ACTIVE_HEALTHY`, Alembic `0065`, размер около 49 МБ.
- Ранее подтверждены pool timeouts с pool size 3. После `88532ed` отдельные пулы web=5,
  background=1, Telegram=3; новых pool timeout в коротком окне не было.
- Advisor указывает на отсутствующие FK covering indexes:
  `ranepa_crawl_jobs.user_id`, `ranepa_materialization_receipts.recipient_user_id`,
  `.source_user_id`, `ranepa_personal_snapshots.user_id`. Добавлять только после EXPLAIN.
- RLS включён без policies на 83 таблицах. Это может быть намеренный backend-only deny-by-default;
  policies нельзя добавлять без проверки grants/Data API.

### Очереди

| Очередь | Состояние | Количество/возраст |
| --- | --- | --- |
| `ai_jobs` | queued | 209, oldest 19 октября |
| `ranepa_crawl_jobs` | queued | 6 с 16 ноября |
| `ranepa_crawl_jobs` | running | 1 с 17 ноября |
| `ranepa_crawl_jobs` | failed | 13; одна CAPTCHA job достигла 28 attempts |
| `bot_outbox` | failed | 25 |
| `image_analysis_jobs` | failed / partial / stale | 41 / 41 / 413 |
| `shadow_evaluation_runs` | failed | 42 |

RANEPA failure breakdown: CAPTCHA 4, source unavailable 5, parse failed 3,
credentials required 1. DeepSeek после прежнего `provider_not_configured` систематически отвечает
`context_insufficient_balance`. До явного подтверждения пополнения запрещены платные canary/replay.

### Производительность

| Endpoint | Наблюдаемое время |
| --- | ---: |
| `/v1/schedule` | 2,8 с |
| `/v1/profile` | 3,4 с |
| `/v1/deadlines` | 7,3 с |
| `/v1/today` | 11,0 с |
| `/v1/homework` | 11,2 с |

### Потери SDO-ссылок

Обезличенный срез `ranepa_personal_snapshots.rows_json`:

| Link state | SDO rows | Rows с candidates | Course/state pairs | Distinct candidates |
| --- | ---: | ---: | ---: | ---: |
| `no_course_links` | **734** | 0 | **3** | 0 |
| `not_found` | 43 | 15 | 8 | 62 |
| `matched` | 37 | 30 | 3 | 138 |
| `timing_unverified` | 37 | 30 | 2 | 84 |

Подтверждены две независимые проблемы:

1. Discovery: три регулярно используемых курса дают 734 строки без единого кандидата, хотя
   владелец видит ссылки в Moodle. Проверить AJAX/dynamic blocks, activity types, iframe/data-url,
   nested section/book/detail pages и bounded traversal. Не расширять allowlist вслепую.
2. Matching/release: кандидаты найдены, но теряются на teacher/group/date/permanent проверке либо
   дальше до materialization/API/UI. Не ослаблять matcher до угадывания.

Обязательная цепочка метрик: `course seen → raw candidates → identity match → timing match →
persisted candidate → confirmed link → recipient materialization → API DTO → frontend action`.
Считать distinct courses/candidates/links/recipients, не повторяющиеся lesson rows.

### Ошибочные owner-уведомления

- `17c7d44d` и `c10892b9`: недели уже подтверждены; timeout около 15 минут возник позже, во время
  SDO link enrichment. Это частичный успех, не полный отказ источника.
- `9225059f` и `b057d5e1`: CAPTCHA solver submitted, но страница месяца не verified; точный
  сетевой сбой не доказан.
- `000f0624`: попытка успешно завершилась примерно за 830 секунд; восстановленное сообщение
  показало 0 недель, потому что отсутствующий historical counter был подменён нулём.

Replay должен явно говорить, что новый обход не запускался; unknown никогда не равен 0. Сообщение
обязано содержать: последний успешный этап, точную известную причину, partial result, retry state,
`next_attempt_at` и действие владельца.

## Реестр проблем

### P0-1/P0-2. Потеря расписания и link-state rollback

Основная реализация слита в PR #194: occurrence-level сохранение опубликованных пар, независимый
link enrichment, единый контракт states. Требуется production canary и контроль нулевых потерь.

### P0-3/P0-4/P0-5. Freshness, representative crawl и materialization

Основная реализация слита в PR #194: six-hour freshness, coverage receipts и безопасная fan-out
materialization. Требуются production SLA metrics, subgroup leak check и rollback drill.

### P0-6/P1-1. AI product outcomes и image isolation

Основная реализация слита в PR #194. Нужны golden evaluation всех групп и доказательство, что
каждый target имеет пользовательский outcome, а самодостаточный текст не ждёт фото.

### P0-7. DeepSeek insufficient balance

До подтверждения владельца не запускать платные запросы. После слов «баланс пополнен» — один
budget-guarded canary, отчёт и остановка. Mass wave/backfill требует отдельного разрешения.

### P0-8. Lost leases и бесконечные retries

Подтверждено production-БД. Исправление реализовано в текущей ветке: error-specific max attempts,
`dead_letter`, lease exhaustion и explicit-only reopening. Acceptance: нет running старше lease,
нет бесконечного retry, каждый terminal job имеет reason и owner action.

### P0-9. API latency до 11 секунд

Добавить privacy-safe stage timings, EXPLAIN/BUFFERS, проверить N+1 и повторную materialization.
Индексы — только после доказанного плана. Gate: p95 warm ≤1s, cold ≤2s без pool exhaustion.

### P0-10. Нет readiness gate и независимого critical worker

Настроить side-effect-free `/ready`, Render health path, worker heartbeat и missed-slot alert.
Критический processing не должен зависеть от web sleep/deploy.

### P0-11. Historical notifications подменяют unknown нулём

Исправление реализовано в текущей ветке: durable privacy-safe outcome/counters, `pending` recovery через
outbox dedup, partial-success heading и запрет legacy auto-replay. Acceptance: 100% новых outcome
имеют stage/code/retry/action; unknown не отображается как ноль; recovery не дублирует сообщение.

### P1-2. Низкий recall SDO links

Создать отдельный PR после P0-11: DOM fixtures трёх проблемных курсов, stage counters, dynamic
stability waits, bounded detail traversal, точная reason taxonomy для 146 unmatched candidates и
end-to-end test до frontend action. `course_url` никогда не является Join.

### P1-3/P1-4. Неполная freshness и грубый retry

Freshness хранить отдельно для schedule/links/source/materialization. Retry различать по CAPTCHA,
source, parse, identity и credentials; user action не повторять автоматически.

### P1-5/P1-7. Sleeping worker и release divergence

Синхронизировать backend/frontend/contracts/migrations SHA, автоматизировать или строго gate-ить
backend deploy, не считать merge/deploy accepted доказательством live/ready.

### P1-6/P2. Админка, owner reports и YDB consistency

Админка должна показывать product outcomes, queue age, dead letters, link conversion, freshness,
CAPTCHA rate и причины пропуска без PII. YDB consistency сравнивает только opaque IDs и агрегаты.

## Обязательные тесты текущего PR

1. Missing historical counters показываются как unknown, без зелёного success и ложных нулей.
2. Timeout после saved weeks сообщает partial success и стадию link enrichment.
3. Crash после commit outcome и до enqueue восстанавливает ровно одно сообщение.
4. Legacy observation с `notification_state=NULL` не replay-ится.
5. Каждый failure type останавливается на своём max attempts и становится `dead_letter`.
6. Login failure остаётся credentials-required до действия пользователя.
7. Repeated expired lease не блокирует следующий eligible job.
8. Обычный scheduler не открывает dead letter; scoped force refresh может.
9. Migration 0065→0066 и downgrade проходят на disposable PostgreSQL.
10. Ruff, полный backend suite и CI зелёные на точном head SHA.

## Правила выпуска

- Не удалять backlog и snapshots массово.
- Не запускать replay/backfill до исправления contracts и queue terminal semantics.
- Migration, production deploy/restart, платный canary и merge — только по отдельному подтверждению.
- PR обязан содержать problem IDs, user effect, exact tests, migration/rollback, production risk,
  canary plan и обновление этого аудита/checkpoint.
