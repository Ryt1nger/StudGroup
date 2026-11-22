# Промт и живой checkpoint восстановления StudGroup

Назначение: этот файл можно целиком вставить в новый рабочий чат. Он должен позволить продолжить
работу без повторного аудита и без догадок о том, что уже сделано. Обновлять после каждого
завершённого этапа, тестового прогона, коммита, PR и обнаруженного блокера.

```text
Ты — ведущий backend/reliability инженер StudGroup. Работай автономно до полностью проверенного
небольшого PR. Владелец подтверждает только product decisions, production mutations, платные
canary/backfill, merge и deploy. Пиши по-русски простым языком.

ЖИВОЙ CHECKPOINT
Последнее обновление: 22 ноября 2026 года, Europe/Moscow.
Прогресс: 65%.
Текущая ветка: backend/performance-diagnostics.
База ветки: main = a918afa (merged PR #208).
Последний подтверждённый production backend: 88532ed (PR #204).
Production отстаёт от main на три commits:
- a505f7e — owner notification для каждой завершённой SDO-учётки (#205);
- 7b2fcee — reuse подтверждённых recurring SDO links (#206);
- 87d047c — точная CAPTCHA classification и optional Moodle deadlines (#207).

Текущее состояние работы:
- чистый актуальный репозиторий восстановлен в новой среде;
- changeset PR-1 повторно применён на `87d047c`;
- audit дополнен production-срезом 20 ноября, P0-8..P0-11 и link-loss данными;
- реализованы migration 0066, durable outcomes, pending notification recovery, bounded retries,
  dead-letter semantics, partial/unknown notification format, workflow schema gate и ADR-022;
- pending ставится только при включённых per-account notifications; legacy/disabled rows не replay;
- `ranepa-crawl.yml` блокирует браузер и student access, пока schema не `0066`;
- backend workflow проверяет downgrade `0066→0065` и повторный upgrade до head;
- Ruff/format: 432 backend files, green;
- targeted: 87 passed;
- full backend: 1934 passed, 37 skipped, 0 failed за 274.74s;
- Alembic graph: одна head `0066`;
- GitHub Backend workflow #3010 успешно прошёл на head `9b5dc4a`, включая disposable PostgreSQL
  rehearsal `0066→0065→0066/head` и полный backend suite;
- production, платные провайдеры и очереди не изменялись.
- локальные commits: `6120985` (реализация) и `27afeb0` (checkpoint);
- ветка опубликована через подключённый GitHub-доступ одним эквивалентным удалённым commit:
  `85824ef24a1faf1dd04148f775bc103664f20886`;
- PR #208 слит 22 ноября 2026 года squash commit `a918afa`:
  https://github.com/Ryt1nger/StudGroup/pull/208;
- production migration/deploy не выполнялись, production остаётся без изменений;
- Backend CI #3010 завершён с `success` на code-equivalent head `9b5dc4a`;
- это обновление checkpoint меняет только документацию и не меняет проверенный backend changeset;
- финальный Backend CI #3011 завершён с `success` на head `634acd0`;
- создана чистая ветка PR-2 `backend/performance-diagnostics` от `a918afa`;
- PR-2 начат с read-only анализа `/v1/schedule`, `/v1/profile`, `/v1/deadlines`, `/v1/today`
  и `/v1/homework`; индексы запрещено добавлять без EXPLAIN/BUFFERS и теста запроса.
- production Supabase read-only анализ подтвердил: таблицы малы, а релевантные SQL-запросы обычно
  выполняются быстрее 1 ms; основная причина 7–11 секунд — большое число последовательных сетевых
  round trips/N+1, а не тяжёлый SQL;
- четыре FK без отдельного индекса проверены; существующие composite indexes уже покрывают основные
  реальные запросы, поэтому недоказанные индексы не добавлялись;
- в PR-2 реализована request-local cache аутентификации и set-based загрузка карточек homework,
  today и deadlines: зависимости карточек, источники, материалы, revisions, вложения, completion,
  subgroup и permissions больше не читаются отдельным набором запросов на каждую карточку;
- измерения SQLite regression tests: homework list из 5 карточек — 43 → не более 16 SQL statements;
  today из 3 карточек — 29 → 20; deadlines из 5 событий удерживаются в бюджете 13;
- добавлена privacy-safe строка `ACADEMIC_API_TIMING` с `db_queries` и `db_ms`: она не содержит SQL,
  параметров, токенов, URL, идентификаторов или текста студентов и отделяет DB latency от YDB/HTTP;
- targeted performance/contract suite: 85 passed; отдельные telemetry tests: 3 passed;
- полный backend после PR-2 изменений: 1938 passed, 37 skipped, 0 failed за 272.13s;
- Ruff и `git diff --check` проходят; changeset закоммичен локально как `d32fb70`, но публикация
  ветки была остановлена защитой среды до явного подтверждения отправки кода в GitHub;
- production не изменён, deploy/restart/migration не выполнялись.

CHANGSET PR-1 — СЛИТ, PRODUCTION ЕЩЁ НЕ ОБНОВЛЁН
1. ranepa_crawl_jobs.py:
   - MAX_ATTEMPTS: source=5, parse=4, identity=3, internal=3, captcha=5;
   - terminal_failure(); retry_at() возвращает None при исчерпании бюджета;
   - exhausted jobs переходят в dead_letter с attempts_exhausted:<code>;
   - login_failed остаётся failed/credentials_required и ждёт действия пользователя;
   - повторно истёкший lease становится dead_letter/lease_expired_exhausted;
   - обычный scheduler не открывает dead_letter, explicit scoped force_refresh может.
2. ranepa_crawl_notifications.py:
   - stats допускает None для исторического события;
   - unknown counters показываются как «нет данных в истории», никогда как 0;
   - failure после сохранённых недель показывается как частичный успех;
   - historical_replay явно сообщает, что новый обход не запускался;
   - exhausted retry сообщает о ручной проверке, а не обещает новый автоповтор;
   - recover_account_crawl_notifications() восстанавливает только новые pending outcomes через
     тот же typed formatter и существующий outbox dedup key.
3. models.py + migration 0066:
   - в ranepa_crawl_observations добавить nullable failure_detail, diagnostic_code, last_step,
     saved_weeks, saved_lessons, saved_sdo_links, discovered_sdo_candidates,
     saved_sdo_courses, notification_state + index;
   - pending ставится только при включённых per-account notifications;
   - legacy/notification-disabled rows имеют notification_state=NULL и автоматически не replay-ятся.
4. ranepa_crawl_trace.py:
   - last_notification_step(); observation(..., stats=None) сохраняет privacy-safe outcome и
     notification_state=pending.
5. ranepa_worker.py:
   - observation получает account_stats;
   - перед batch/pilot безопасно вызывается recovery; ошибка recovery логируется и не блокирует crawl.
6. Тесты:
   - unknown history без ложных нулей и без зелёного success;
   - partial SDO timeout с сохранённым расписанием и точной стадией;
   - recovery после crash между commit результата и enqueue уведомления, ровно один outbox item;
   - attempt exhaustion/dead_letter, explicit force refresh;
   - repeated expired lease не блокирует следующий job.
7. Документация:
   - ADR-022 durable outcomes and bounded retries;
   - release checklist обновить до migration 0066;
   - audit обновить фактами ниже.

ПОДТВЕРЖДЁННЫЙ PRODUCTION BASELINE НА 20 НОЯБРЯ
- ai_jobs: done 1060, queued 209, oldest queued 19 октября;
- RANEPA: queued 6, running 1 с 17 ноября, failed 13; одна CAPTCHA job — 28 attempts;
- bot_outbox failed 25; image failed 41, partial 41, ready 297, stale 413;
- DeepSeek: context_insufficient_balance; платный canary запрещён до слов владельца
  «баланс пополнен, разрешаю один canary»;
- API: schedule 2.8s, profile 3.4s, deadlines 7.3s, today 11.0s, homework 11.2s;
- Render health-check path не настроен, backend autoDeploy=no, отдельного worker нет;
- Supabase schema 0065, DB ~49MB; missing FK covering indexes:
  ranepa_crawl_jobs.user_id, materialization receipts recipient/source, snapshots.user_id.

SDO LINKS — ПОДТВЕРЖДЁННЫЕ ПОТЕРИ
- no_course_links: 734 lesson rows, всего 3 course/state пары, 0 candidates;
- not_found: 43 rows, 62 distinct candidates;
- timing_unverified: 37 rows, 84 distinct candidates;
- matched: 37 rows, 138 distinct candidates.
Проверять цепочку: course seen → raw candidates → identity/timing match → persisted candidate →
confirmed link → recipient materialization → API DTO → frontend action. Не угадывать link при
неоднозначности и никогда не выдавать course_url как Join.

УВЕДОМЛЕНИЯ — ПОДТВЕРЖДЁННЫЕ ПРИМЕРЫ
- 17c7d44d и c10892b9: недели уже verified, затем ~15-minute timeout на SDO enrichment;
  это partial success, не полный source failure;
- 9225059f и b057d5e1: solver submitted, но month page не verified; сеть не доказана;
- 000f0624: success около 830s, историческое сообщение показало 0 из-за отсутствующего counter.

ФОРМАТ КАЖДОГО ОТЧЁТА
Прогресс: [████░░░░░░░░░░░░░░░░] 20%
Текущий этап: ...
Сейчас исправляю: ...
Уже подтверждено: ...
Риск/блокер: ...
Следующий шаг: ...
Нужно от владельца: ничего / одно точное решение.
20 блоков, каждый = 5%. Процент повышается только по доказательствам.

ПОРЯДОК РАБОТ
PR-1 queue recovery + P0-11 notifications.
PR-2 DB/API performance и доказанные indexes.
PR-3 полный SDO link discovery/matcher/delivery pipeline.
PR-4 AI/image outcomes и DeepSeek recovery после разрешения.
PR-5 runtime/readiness/independent worker/release consistency.
PR-6 admin/YDB/reporting и полный operational gate.

QUALITY GATES
- 8/10: нет P0, stale leases, необъяснимого backlog; p95 warm <=1s/cold <=2s; полный CI и
  PostgreSQL integration green; readiness; canary без дублей/потерь; rollback проверен.
- 9/10: P1 закрыты/приняты; independent worker; 95% freshness SLA; 7 дней без missed slots,
  pool exhaustion и stale leases; alerting испытан.
- 10/10: 30 дней SLO, restore/DR drills, no high security findings, capacity test с запасом.
Не завышать оценку без этих доказательств. Текущая честная оценка: около 4/10.

БЕЗОПАСНОСТЬ И GIT
- Не читать/не использовать "StudGroup documentation v0.3".
- Не коммитить secrets, URLs с tokens, cookies, CAPTCHA images или private text.
- Никаких mass replay/backfill, production migration/deploy/restart, платного canary или merge без
  отдельного подтверждения владельца.
- Маленькие commits, полный diff review, migrations forward/downgrade plan, exact test results.
- Подготовь PR полностью и остановись на фразе: «Подтвердить merge PR <номер>».

СЛЕДУЮЩИЙ ШАГ
После явного подтверждения владельца опубликуй commit `d32fb70` в доверенный репозиторий
`Ryt1nger/StudGroup`, открой небольшой PR только для устранения N+1 в homework/today/deadlines и
privacy-safe DB timing. Дождись CI и остановись перед merge.
В следующем performance changeset используй production timing `db_queries/db_ms`, чтобы отдельно
исправить повторные schedule reads и доказать, что задержка profile относится к DB или внешнему YDB.
Не выполнять merge, production migration/deploy или restart без отдельного подтверждения владельца.
```
