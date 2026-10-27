# StudGroup Core v2 — проверка после решений владельца

Дата: 27 октября 2026. Ветка: `codex/core-image-audit`.

Backend-изменения реализованы. Настоящая модель проверена на 16 разрешённых синтетических PNG;
все расходы и повторы сохранены; действующий общий лимит снижен владельцем до $1. Production, writer mode, реальные группы,
история переписки и публичные функции не включались. Архитектура C+ сохранена.

## Новая поставка по финальному ТЗ — этап 0

Проверка 27.10.2026 выполнена read-only. Production-развёртывание теперь разрешено владельцем
условно: после всех обязательных проверок и backup. Владелец явно назначил единственную подключённую группу «Тестирование StudGroup · БИ 1.2»;
технический ID восстанавливается из сохранённой конфигурации, повторного запроса владельцу нет.
Единый новый hard cap — USD 1 на всю работу, включая прежний консервативный учёт USD 0.181507560;
до новых вызовов остаётся максимум USD 0.818492440 на сравнения и пилот вместе. Журнал не обнуляется.
Владелец прямым поручением передал интеграцию событий/календаря Codex; frontend-правки ограничены
этими сценариями и подтверждением расписания, чужая параллельная работа не перезаписывается.

| Объект | Проверенное состояние |
|---|---|
| origin/main | dbc59638bcde89daec29e2d71c6c51bb95c29d54; fetch успешен |
| Рабочая ветка | codex/core-image-audit, 51bc648; чистая, 19 коммитов поверх main на старте |
| Production backend | f95c516a6cbc39e9177ddc5c79c6302488f80fdb, deploy dep-dbe9ktuk1f9s73f316v0 live, 24.10.2026 11:48:06 UTC |
| Production frontend | 68e72c76dfea5e8c4c9a60e4091be4691a138273, deploy dep-dbdo3tu7bikc73e9uos0 live, 23.10.2026 15:49:32 UTC |
| Runtime | Render Free, один embedded API-процесс; startup применяет Alembic перед Uvicorn |
| Проверка доступности | backend /health и /ready, frontend / — HTTP 200 |
| Настройки env | PHOTO_READING_ENABLED=true, AI_ENABLED=false, AI_SHADOW_ENABLED=false; image-флаги отсутствуют в текущем env, код v2 не развёрнут |
| PostgreSQL | available, v17, external allowlist пуст; истечение free DB 05.11.2026 |
| GitHub | Ryt1nger, admin/push подтверждены через connector; fetch успешен |
| Защита от повторных обновлений | интегрирована из codex/context-novelty-guard, 6b5774c/28a09fb/91852a9/f8eedf4; не развёрнута |

Landing на отдельной ветке landing не входит в поставку. В исходной рабочей копии есть чужие
незакоммиченные документы, design-demo и landing — они не изменялись. Ветки/режимы групп в
БД пока не подтверждены прямым SQL: внешняя БД закрыта, ослаблять allowlist нельзя. Проверенной
процедуры production backup пока нет; это обязательный gate перед deployment. Никаких новых
платных AI-вызовов в этой поставке пока нет. Новый корпус разрешён финальным ТЗ, общий hard cap уточнён владельцем до USD 1; старые расходы включены в него.

### Этап 1 — локальное покрытие изображений

На исходных пикселях прежнего корпуса и сохранённых чтениях без новых AI-вызовов:
three_crops выбирает только фото 3, four_pages сохраняет 4 страницы. Семантический вход и
доказательства по всем исходникам не фильтруются. Покрытие требует полного сопоставления
полос чернил с текстовыми строками, порядка слов, совпадающих бинарных масок слов и достаточной
читаемости. Диапазоны расширяются только как явно обозначенные номера упражнений/задач,
а не как арифметическое вычитание. Формулы, таблицы, неизвестные схемы, конфликт A/B и
неподтверждённая геометрия не скрываются. Perceptual hash сам по себе больше не удаляет кадр.

Добавлена nullable metadata в миграции 0045, срок хранения исходника не продлевается.
Маски остаются только в image job; компактные display edges сохраняются в snapshot для
одинаковых preview/student-результатов и исключаются из обоих AI-запросов. Нет вызовов
модели, скачивания фотографий или повторного OCR при открытии карточки.

Проверки: 12 новых проверок покрытия; вместе с исходными display/worker/semantics — 50 passed
до дополнительного subtraction-теста; заключительный coverage + реальный PostgreSQL —
17 passed, 0 skipped. Миграция 0044 → 0045 → 0044 → 0045 прошла. Ruff и diff-check чисты.
Метод намеренно консервативен: наклонённый/сложный снимок без доказательства покрытия
остаётся видимым. Качество на новом сложном корпусе ещё предстоит измерить.

### Этап 2 — повторное использование и контекст

Миграция 0046 сохраняет идентичность модели/настроенной ревизии/схемы и ссылку на исходное
чтение. Между разными источниками проход A переиспользуется только при явной настройке
`image_model_revision`, в пределах одной группы, совпадающих версии промпта/схемы/модели и
точных байтов либо Telegram file_unique_id. По умолчанию эта оптимизация выключена.
Изменение подписи сохраняет чтение неизменных пикселей, но требует нового B; изменение
модели инвалидирует оба прохода. Ранее успешно проверенный идентичный контекст может снова
стать текущим без покупки ещё одного анализа. Неудачи и неопределённые платные попытки
таким способом не возобновляются. Истёкшие/отозванные источники не используются; срок кеша
не продлевается. Идентичность исключена из AI-payload, но включена в ключи обоих проходов.

Проход B сохраняет текущий источник, текстовые уточнения и reply-связи; пустые соседние
фотографии не создают новый текстовый контекст. Карточки ограничены связями с источником,
события — связями или явным названием. Расписание сокращается только при явном названии
предмета/преподавателя в пикселях; каталог предметов остаётся полным, частичный список
расписания помечается явно. Неподтверждённый предмет не выводится из соседней реплики.

В локальном измерении двух одинаковых изображений провайдер вызван три раза вместо
четырёх: сохранённое A и новый B. Это счётчик тестового провайдера, не измеренная экономия
долларов на реальной модели. Shared ledger теперь применяет общий USD 1, включая прежние
USD 0.181507560; понижение лимита не удаляет историю или неопределённые расходы. Новых
платных вызовов пока нет. Производственный lifetime-cap и пилот ещё предстоит проверить.

Проверки этапа: 58 focused-тестов, включая реальный PostgreSQL, без пропусков; миграция
0046 → 0045 → 0046 успешна. Полный backend-прогон: **1222 passed, 0 failed, 0 skipped**
за 141.91 с; Ruff check/format чисты.

### Интеграция защиты от ложных обновлений

Готовая отдельная реализация context-novelty-guard объединена с Core v2, без второй системы
новизны. Prompt 16 сохраняет точные image-fact references и добавляет target/context роли.
Live-изменение требует целевого источника и фактического дополнения к сохранённой карточке;
формальная ссылка на «Спасибо!» не разрешает пересказ старого контекста. Полные описания
больше не обрезаются на 600 символах, обычный update сохраняет уже вычисленный дедлайн.
После album reconciliation по-прежнему повторно проверяются все визуальные источники.

При объединении исправлен короткий cancel: явная «Отмена!» в прямом ответе на сохранённый
источник этой карточки не требует трёх новых слов. Отдельно проверены благодарность в ответе
и отмена без подтверждённой связи — они не отменяют карточку. 99 focused-тестов и затем
48 package-worker тестов, включая три новых cancel-сценария, прошли без платных вызовов.
Production-поведение пока не изменено.

### События: интеграционный API

Image Findings API 0.3.0 подключён через ссылки из основного OpenAPI 0.19.0, shared types и SDK
пересозданы. Один endpoint обслуживает список и календарные интервалы; перенос/отмена меняют
то же событие. Добавлены парные границы периода (до 366 дней), хронологический порядок,
будущие/завершённые/отменённые записи, безопасная ссылка на retained источник и признак переноса
по опубликованной истории. SessionContext содержит необязательные групповые capabilities,
которые не заменяют серверные проверки прав и флагов. 23 проверки, включая реальный PostgreSQL,
прошли без пропусков. Они подтверждают локальные API-сценарии, а не production-пилот.
Frontend-интеграция выполняется; платных AI-вызовов нет.

Ниже — результаты предыдущего завершённого синтетического этапа; новый итог будет обновлён
здесь же после выполнения финального ТЗ.

## Что изменено

- **События:** опубликованная структура живёт до конца седьмого календарного дня после окончания
  мероприятия по времени группы. Например, событие 20 декабря доступно весь день 27 декабря,
  удаление начинается 28 декабря в 00:00. Источники удаляются отдельно по прежним срокам.
  Проверены даты без времени, явное окончание, перенос, отмена, часовые пояса и мероприятие
  дальше чем через 30 дней. Неизвестная дата и многодневное событие без окончания не публикуются.
- **Раздел событий:** отдельные черновики, подтверждение редактором, групповая аудитория,
  история, защита ручных изменений и повторы по идентичности события. Личное приглашение не
  становится общим событием. После удаления источника API сообщает `source_availability=expired`.
- **Расписание:** редактор исправляет предмет, даты действия, подгруппу, заменяемую пару,
  время, аудиторию, преподавателя и once/recurring. Проверяются права, группа/предмет подгруппы,
  ревизии и ручные блокировки; действует аудит. AI-строки сохраняются отдельно от подтверждённых.
  Отмена разовой правки восстанавливает исходные предмет, преподавателя и подгруппу.
- **Точность источников:** визуальный проход получает ID исходного сообщения, replies и альбомы.
  Факты изображения отделены от смысла, полученного из чата. C+ получает готовые точные ссылки
  на факты. После присоединения страниц альбома источники проверяются повторно: непрочитанная
  страница или отсутствие ссылки удерживают предложение, а не дают опубликовать неполную карточку.
- **Контракты:** аддитивный Image Findings API 0.2.0 описывает ответы событий, предложения и
  подтверждение расписания. Необязательное `subgroup_id` добавлено в тип занятия; shared types
  пересозданы. Миграции 0043–0044 аддитивные.

Backend готов к frontend-интеграции. Интерфейсы событий/подтверждения расписания ещё должны быть
согласованы и реализованы владельцем frontend. Все review/publication-флаги остаются выключенными.

## Как проверялась модель

Использован существующий `deepseek-flash`; официальное имя версии — DeepSeek-V4.1-Flash-1017.
Поддержка PNG через vision API и наличие модели проверены перед платными запросами.
A повторно читает пиксели без чата; B снова получает те же пиксели, результат A и контекст.
После них запускаются существующие два текстовых прохода C+ без записи в production.

Окончательные версии: A3, B6 и C+ extraction 15. Чтение A сохранено и повторно использовано на
неизменных изображениях; B6 проверен на всех 16, C+ — на всех 11 сценариях. Все прежние ответы,
неудачные подключения, оплаченные ответы с ошибкой схемы и повторные проверки сохранены.

Контекст синтетический: текстовые уточнения явно отвечают на первое изображение альбома.
Изначальная gold-разметка содержала текст уточнений, но не полные reply/album-связи; их добавили
в тестовую обвязку. Поэтому ранний прогон не является контролируемым сравнением качества.

**Ограничения корпуса:** это короткий печатный текст, а не студенческие фотографии. Названия
сценариев видны в служебных заголовках PNG и могут подсказывать классификацию. Проверка не слепая.
Нет рукописи, наклонённых снимков, сложных таблиц, формул, длинных документов, настоящего
размытого текста и конфликтующей реальной переписки. Образец «расписание» — одна строка,
а «нечитаемое» — серые полосы без скрытого текста. Эти возможности остаются **НЕ ПРОВЕРЕНЫ**.

## Результаты 16 изображений

Имена ниже соответствуют PNG в `apps/backend/tests/fixtures/ai_analysis/image-pixels/`.
«Ошибки/потери/выдумки» относятся к существенному видимому содержимому окончательного чтения;
ошибки промежуточных прогонов перечислены отдельно. Дополнительные метки material/unknown
допускаются рядом с основной категорией и не считаются отдельными заданиями.

| № / изображение | Тип | Ожидалось | Что прочитал A | Ошибки / потери / выдумки | Класс B | Что дал B | Итог |
|---|---|---|---|---|---|---|---|
| 1 `three_crops-0` | Обрезка | Алгебра, задачи 1–5 | Весь текст и 1–5 | 0 / 0 / 0 | ДЗ | Связал «Дома к среде» с источником | Смысл сохранён; выбор фото избыточен |
| 2 `three_crops-1` | Обрезка | Задачи 6–8 | Весь текст и 6–8 | 0 / 0 / 0 | ДЗ | Отнёс продолжение альбома к тому же заданию | Смысл сохранён; выбор фото избыточен |
| 3 `three_crops-2` | Общая обрезка | Задачи 1–8, PDF | Диапазон и PDF | 0 / 0 / 0 | ДЗ | Сохранил общий диапазон и формат | Смысл сохранён; выбор фото избыточен |
| 4 `four_pages-0` | Страница | Практическая по алгебре, упражнение 1 | Весь текст, номер 1 | 0 / 0 / 0 | ДЗ | Применил «Всё это сделать дома» к альбому | PASS |
| 5 `four_pages-1` | Страница | Упражнение 2 | Весь текст, номер 2 | 0 / 0 / 0 | ДЗ | Исправил предварительный уклон A к работе на паре | PASS |
| 6 `four_pages-2` | Страница | Упражнение 3 | Весь текст, номер 3 | 0 / 0 / 0 | ДЗ | Установил назначение по явному ответу | PASS |
| 7 `four_pages-3` | Страница | Упражнение 4 | Весь текст, номер 4 | 0 / 0 / 0 | ДЗ | Исправил предварительный уклон A к работе на паре | PASS |
| 8 `in_class-0` | Задание на паре | Алгебра 1–8, «Решаем сейчас на паре» | Диапазон 1–8 | 0 / 0 / 0 | Работа на паре | Учёл чат; не создал ДЗ | PASS |
| 9 `finish_at_home-0` | Остаток задания | Доделать неизвестный остаток из 1–8 к среде | Весь диапазон 1–8 | 0 / 0 / 0 в B; результат C+ ниже | ДЗ | Сохранил именно незавершённую часть, не назначил весь диапазон повторно | PASS |
| 10 `ambiguous_exercises-0` | Упражнения без контекста | 1–8, назначение неизвестно | Текст прочитан; A ошибочно complete=false | Флаг A исправлен / 0 / 0 | Неизвестно | B6 отделил читаемость от назначения: complete=true, без ДЗ | PASS чтения; интерфейс уточнения не проверен |
| 11 `schedule-0` | Строка расписания | Пн 09:00–10:30, Алгебра, 201, подгруппа 1 | Все значения | 0 / 0 / 0 | Расписание | Структурировал строку; период оставил неизвестным | PASS строки; сложная таблица не проверена |
| 12 `poster-0` | Афиша | 28.10.2026 18:00, лекция о космосе, актовый зал, вся группа | Все сведения | 0 / 0 / 0 | Событие | Структура, время +03:00, групповая аудитория | PASS; только черновик |
| 13 `personal_poster-0` | Личное приглашение | Та же лекция; приглашение только Ивану | Дата и время | 0 / 0 / 0 | Событие, personal | Учёл личную аудиторию из сообщения 100 | PASS; общей публикации нет |
| 14 `mixed_schedule_check-0` | Смешанное объявление | Перенос на 11:00 и контрольная по главе 2, дата неизвестна | Оба факта, 11:00 и глава 2 | 0 / 0 / 0 | Перенос + проверка | Разделил смыслы; дату не придумал | PASS; расписание автоматически не менялось |
| 15 `correction-0` | ДЗ с уточнением | На фото 1–5; пункт 5 отменён в чате | Исходные 1–5 | 0 / 0 / 0 | ДЗ | Действующее задание 1–4, пункт 5 не нужен | PASS |
| 16 `unreadable-0` | Серые полосы | Нет читаемых условий | Служебная надпись и полосы | Содержимое не выдумано | Неизвестно | complete=false; без задания | PASS безопасного отказа на этом образце |

В 15 читаемых изображениях существенные напечатанные строки/числа сохранены. Это ручная сверка
малого синтетического набора, не статистическая оценка OCR precision/recall на реальных фото.

### Объединённые предложения карточек и вложения

Это результаты локального C+, а не опубликованные карточки студентов.

- **Три обрезки:** Решить дома задачи 1–8 по алгебре и оформить работу в формате PDF. Срок: 28.10.2026, date_only; исходное «к среде»
  привязано к 27.10.2026 по времени группы. Выбраны `three_crops-0`, `-1`, `-2`.
  Уникальные факты и PDF сохранены. На этих простых PNG третья картинка уже содержит всё;
  сохранение всех трёх избыточно. Минимальный достаточный набор пока не достигнут.
- **Четыре страницы:** Выполнить дома практическую работу по алгебре — упражнения 1, 2, 3 и 4. Выбраны все `four_pages-0…3`;
  одна карточка, упражнения 1–4 сохранены. Срок не выдуман.
- **Доделать дома:** Доделать дома оставшуюся, незавершённую часть упражнений по алгебре из напечатанного списка 1–8. Какие именно номера остались незавершёнными, не указано; выполнять все упражнения диапазона не требуется. Срок: 28.10.2026, date_only.
- **Уточнение:** Выполнить пункты 1–4 домашней работы; пункт 5 выполнять не нужно. Предмет и срок остаются неизвестными.

В окончательном C+ получено 4/4 ожидаемых домашних заданий, 0 ложных ДЗ в остальных 7 сценариях. Смешанное объявление отдельно дало проверочную работу без придуманной даты. Общие и личные события остались кандидатами/черновиками; официальное расписание не менялось. Точные image-ссылки всех принятых карточек вручную сопоставлены с фактическими номерами/условиями, а чатовые уточнения с их собственными сообщениями.

## Что изменилось между A и B

На этих коротких текстах B не нашёл новых потерянных цифр или условий: A уже прочитал их.
Польза B здесь — контекст и структура: отличить ДЗ от работы на паре, связать страницы,
сохранить отмену пункта 5, выделить подгруппу расписания, аудиторию события и смешанную проверку.
B6 также исправил ошибочный флаг неполноты у читаемых упражнений без установленного назначения.

В промежуточных версиях B появились реальные ошибки: текст чата становился «фактом изображения»;
«доделать остальное» превращалось в «выполнить всё 1–8»; неизвестное назначение трактовалось как
неполное чтение. Уточнены правила происхождения фактов, объёма работы и визуальной полноты.
Строгая схема отклоняла лишние поля `title` внутри блока и timestamp без offset; эти случаи
исправлены в запросах, ограничители не ослаблялись.

C+ иногда забывает точную ссылку даже при правильном описании. В полном прогоне extraction 14
одна карточка «доделать дома» была удержана новым guard. Extraction 15 дополнительно проверяет
это в конце инструкции. Все неудачные ответы оставлены в журнале. Успех повтора не доказывает,
что модель всегда соблюдает контракт; backend продолжает удерживать такие результаты.

Для окончательных 16 пар A стоил $0.010056, B — $0.007940. При одинаковом peak-тарифе это $0.011212 и $0.015879: B дороже из-за дополнительного контекста и ответа. Средняя задержка успешного A — 2.524 с, B — 3.438 с. Это отдельные последовательные вызовы; полное время очереди не включено.

Второй проход оправдан как проверяемый источник контекстного смысла на этом наборе. Его
преимущество именно в OCR относительно одного прохода **не доказано**; сравнение старого
production-пути и нового пути одной настоящей моделью также не проводилось. Нельзя обещать
такую же точность или выигрыш на рукописи/сложных таблицах.

## Расходы и задержка

| Показатель | Результат |
|---|---|
| Модель | deepseek-flash / DeepSeek-V4.1-Flash-1017 |
| Все попытки за настройку и проверку | 278 |
| Ответы модели с usage | 216 |
| Сбои подключения до платного запроса | 60 |
| Неопределённые исходы | 2 |
| Input / cache hit / output tokens | 788,069 / 492,160 / 62,259 |
| Подтверждённая оценка по usage | $0.106029180 |
| Возможное дополнение по неопределённым исходам | до $0.0150711; резерв сохранён |
| Общий консервативный учёт по peak с резервами | $0.181507560 из $2 |
| Окончательные A+B: 32 успешных вызова | $0.017995266; $0.001124704 за изображение |
| Окончательный C+: 22 успешных вызова | $0.007315920 |
| Окончательная выборка A+B+C+ | $0.025311186; не включает настройку и неудачные попытки |
| Все подтверждённые затраты / 16 изображений | $0.006626824; включает настройку, не является обычной ценой фото |
| Средняя задержка ответившего вызова | 2.573 с |
| Средняя задержка всех попыток | 4.794 с |
| Два текстовых прохода C+ на сценарий | 5.092 с в среднем без очереди |

Тариф проверен по [документации DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/):
за миллион токенов peak $0.30 input miss / $0.006 hit / $1.20 output, off-peak вдвое дешевле.
Использованы времена начала вызовов UTC; это оценка по реальному usage, без сверки с invoice.
Для лимита весь запуск считался консервативно по peak. При неопределённом исходе полный резерв
остаётся занятым; его не обнуляли перед повтором. Ручные повторы допустимы только в этом
изолированном синтетическом тесте и том же $2-журнале. Production hold не менялся.

Сначала локальный TLS часто не устанавливался; эти подключения не стали AI-ответами.
В тестовом транспорте добавлены HTTP/2 и ограниченные повторы только ConnectError/ConnectTimeout,
до отправки HTTP. Ошибки чтения/записи оплаченного запроса автоматически не повторяются.
Поведение соответствует [документации HTTPX](https://www.python-httpx.org/advanced/transports/).
Это не измерение доступности production-провайдера и не SLA бота. Паузы в работе над тестом
не входят в среднюю задержку вызова; полное время Telegram → карточка не измерялось.

Журнал всех попыток, raw-ответы модели, usage, версии, результаты и hash входного manifest:
[image-provider-observations.json](../../apps/backend/tests/fixtures/ai_analysis/image-provider-observations.json).
Ключей API, приватной переписки и реальных фотографий в нём нет.

## Проверки реализации

- Последний полный backend-прогон: **1202 passed, 0 failed, 0 skipped**, PostgreSQL включён.
  В нём **31 PostgreSQL-тест**; это подмножество общего числа, не добавка.
- После последнего изменения prompt 15: **78 профильных тестов прошли**. Повторы не суммируются
  как уникальные тесты. Отдельно прошли проверки бюджетного журнала и сохранения неудачных
  изображений в синтетическом входе C+.
- Миграции: чистая база прошла base → 0044; цикл 0044 → 0042 → 0044 прошёл.
- `ruff check .`, `ruff format --check .` и `git diff --check` прошли. Приведён к формату ранее
  добавленный image-код; основная часть большого diff форматирования не меняет поведение.
- Main OpenAPI: генерация shared types и `tsc --noEmit` прошли. Image Findings 0.2.0: YAML/API
  generator dry-run прошёл; запросы, роли, ревизии, ответы событий и подтверждение расписания
  проверены backend API-тестами. Это не проверка ещё не реализованного frontend.
- Проверки безопасности: повторная авторизация, чужая группа/подгруппа, отсутствие сессии,
  личная аудитория, stale source/revision, ручные блокировки, удалённые источники и бюджетные
  ограничения проверены тестами. Отдельный внешний пентест не проводился.

## Готовность и следующий пилот

1. **Чтение изображений:** новый путь сохраняет полные факты этих простых PNG и передаёт их в C+.
   Превосходство OCR над старой живой системой не измерено.
2. **Обрезки:** в двух сценариях альбомы объединились без потери уникального содержания;
   обобщение на реальные снимки пока не доказано.
3. **Категории:** на проверенных примерах различаются ДЗ, работа на паре, неизвестное назначение,
   расписание и события. Безопасное удержание остаётся обязательным при ошибке модели.
4. **Фотографии карточки:** четыре разные страницы сохраняются; три перекрывающиеся обрезки
   ещё выбираются избыточно. Приоритет полноты соблюдён, оптимизация минимального набора не завершена.
5. **Пилот:** можно предложить только ограниченный shadow для измерений после отдельного
   разрешения. Готовность к автоматической публикации на реальных данных этим тестом не доказана.

Предложение будущего пилота: одна явно выбранная группа, 48 часов, только сообщения после
согласованного cutoff, максимум 20 новых изображений в сутки / 40 всего. Общий лимит $2,
$1 в сутки и $0.25 в час вместе с C+; ни history backfill, ни writer-mode switch, ни публичные
события/расписание не включать. Остановить pilot при неизвестном платном исходе, выходе за
бюджет, потере критического факта, неверной ссылке или ложном ДЗ. После остановки — ручной
разбор, без автоматической повторной покупки. Откат: выключить image-флаги, сохранить
ограниченный по сроку журнал; таблицы и текущие карточки не удалять.

Каждый из максимум 40 примеров нужно вручную разметить и проверить числа/даты, полноту альбома,
категорию, аудиторию и точные источники. Порог для обсуждения live: ни одного выдуманного
критического условия/даты, неверной аудитории, ложного ДЗ или неполной принятой карточки.
Сложные таблицы и рукопись требуют отдельного явно разрешённого набора; этот тест их не заменяет.

## Вопросы владельцу

Новых решений по событиям или расписанию не требуется: утверждённые правила реализованы.
Пилот не запускался. Для него позже потребуются отдельное разрешение, конкретная группа и дата
начала. Frontend-интеграция и публичное включение остаются отдельным согласованным этапом.
Утренний инцидент повторных обновлений передан в другой чат и не смешан с этой реализацией.

---

## Техническое основание: аудит исходного пути и реализация офлайн-части

Следующие разделы описывают исходную систему и технические этапы 0–7. Актуальные результаты
платной синтетической проверки и решения владельца приведены выше.

### Исходная техническая запись

Updated: 2026-10-27. Scope: stages 0–7 of the Core v2 image-quality brief; evolutionary C+ changes only.
Owner decisions of 2026-10-27 are implemented; synthetic-only paid checks are authorized up to $2 and recorded below. Real-group activation and production deployment are not approved.
Source: `origin/main` at `dbc59638bcde89daec29e2d71c6c51bb95c29d54`.
Working branch: `codex/core-image-audit`, in a separate managed worktree.

## Baseline and scope

The original checkout is `codex/ai-analysis-core`, one commit ahead and 82 behind current
`origin/main`, with unrelated uncommitted files. Local `main` is 20 commits behind
`origin/main`. Fetch succeeded. The audit uses current remote main without merging, resetting
or changing the original checkout. At baseline no production configuration, provider call, published card, frontend file or shared API contract had been changed. Subsequent additive contracts and authorized synthetic calls are described below. Obsolete documentation was not used.

This report replaces the old integration snapshot in place. The implementation plan is in
[the current roadmap](ai-analysis-rebuild-roadmap.md#18-core-v2-image-quality-work).
The system already has a live package writer, durable shadow execution and canonical projection;
the earlier integration report's missing-writer blockers no longer describe this source revision.

## Stage-0 baseline source-to-student path

1. `ingestion.py:attachment_metadata`, `store_message` and `sync_attachments` persist media-only
   messages, the largest Telegram photo variant, document metadata, album IDs and attachment
   revision history. The multiple Telegram photo sizes are variants of one image, not pages.
2. `analysis/dispatch.py` selects shadow, package/new or explicitly enabled legacy execution.
3. `package_worker.py:_plan_next` gathers targets and bounded surrounding messages. Before
   creating a run, `_read_pending_photos` downloads and reads up to six photo attachments.
4. `photo_reading.py:read_photo` sends pixels and at most 500 caption characters to ONE vision
   call, requests at most 900 output tokens and parses relevance/summary/text. There is no
   second visual call and no chat context, existing cards or timetable in this visual request.
5. `_source_messages` uses `photo_dedupe.distinct`, including 14 days of older anchors, to change
   duplicate input attachments to `irrelevant`. `enriched_text` then excludes their OCR.
   The stored attachment state remains intact; loss occurs in the semantic input snapshot.
6. `run_shadow` performs Context Understanding and Academic Extraction, both text-only.
   Adapter, strict validation, evidence checks, canonical resolver/writer, deadline policy,
   manual locks and compatibility projections remain the existing C+ foundation.
7. `student_cards.py:projected_materials` independently loads active attachments from cited
   messages and runs `distinct` again. A ready photo with caption <=120 characters has
   `replaces_description=true`. The existing public contract explicitly tells the client to
   replace the text description with this photo.

## Confirmed defects and limits

| Finding | Source / deterministic evidence | Consequence |
|---|---|---|
| Deduplication before semantic merge | `package_worker._source_messages`; three-crop baseline | All 3 pictures are read, but only 1 reading reaches extraction; unique facts disappear. |
| Text similarity mistaken for visual equality | `photo_dedupe.words/same_content`; four-slide baseline | Single-digit numbers are discarded; 4 distinct exercises collapse to 1. Older anchors can suppress a better new crop regardless of its extra content. No pixels enter deduplication. |
| Incomplete packages marked completed | `_read_pending_photos` slices to 6; 1/6/7/12 baseline | 7 pictures leave 1 unread, 12 leave 6 unread, while every source is completed. Next sweep is idle without configured backfill. |
| Backfill is conditional, not guaranteed continuation | `reopen_unread_photo_messages`, `photo_backfill_since` | Configured backfill can reopen eligible non-imported messages repeatedly until no metadata-only photo remains. It is not correct to claim every seventh photo is always lost permanently. |
| Image documents not read | pending query requires `kind=photo`; PNG-document baseline | Document is retained but receives zero vision attempts. |
| Truncation without explicit incomplete status | `parse_reading`, `enriched_text`, `SourceMessage` | OCR silently sliced at 2500 characters, summary at 300, enriched message at 12000. A valid JSON response is not checked for `finish_reason=length`. |
| Insufficient visual semantics | `PHOTO_PROMPT` categories | No schedule/in-class categories, mixed types or structured per-image facts. Academic checks are merged into image category event. |
| Photo facts discouraged downstream | `extraction_strategy._EXTRACTION_INSTRUCTIONS` | Prompt tells extraction to keep photo descriptions general and avoid exact numbers. This directly conflicts with complete descriptions in the new brief. |
| Errors treated as completed package input | download/MIME/non-retryable failure paths; existing tests | Unavailable Telegram downloads are final `failed` on first attempt. No durable error code, per-image retry counter or distinction between temporary and terminal download errors. A package may continue to proposals without readable photo evidence. |
| Retryable vision has no image attempt limit/backoff | `_read_pending_photos` returns `photo_retry` before creating run | Text-run attempt limits do not cap these repeated image calls. Successful earlier readings are retained, but their charge is lost on this retry path. |
| Vision before budget reservation | `_plan_next` runs before `_claim`; zero-budget baseline | Even a group with zero budget receives a vision call, then its text run is deferred. No per-image cap/reservation. |
| Costs only partially observable | local `photo_cost` added to run at enqueue | Vision tokens/latency/version are not stored per image; successful earlier work costs disappear on `photo_retry`. A crash before commit can repeat a paid call. |
| Context and version limitations | `_source_messages`, `SourceAttachment`, attachment model | OCR is merged into message text without attachment IDs or fact-level image evidence. Caption-only edits leave old photo reading active; any future contextual pass must invalidate by context revision too. |
| Different presentation paths | `card_materials._from_message` vs `student_cards.projected_materials` | Preview derives materials from citations/replies/albums with `contents_read=false`; API derives from canonical cited sources and independently suppresses photos. No shared per-image selection result. |

The package group-settings row lock serializes planning in PostgreSQL. Existing concurrency tests
prove package-level idempotency, not crash-safe paid image analysis: no independently committed
per-pass lease/checkpoint exists. Preserve these locks while adding image jobs.
The source attachment query and anchor query lack explicit attachment expiry/source-revision
predicates; future versioned image results must check group, active source and context revisions,
retention and lease ownership at both claim and persistence.

## Stage-0 baseline: shadow, preview, events and schedule

- Shadow source preparation (`processing` / `academic_context`, `shadow_worker.enqueue_shadow`)
  supplies attachment metadata and original text, without the live photo-reading enrichment.
  Owner preview replays stored results under a rolled-back savepoint; it does not run vision.
  Comparing current shadow and live results is therefore not a same-image-input comparison.
- `candidates.event_records` already provides unpublished `EventRecord` DTOs with verified
  sources/audience blockers. Storage is the run JSON with 30-day retention. It is not a durable
  public event lifecycle or student endpoint. Existing graded-event/deadline endpoints are a
  different product entity; do not reuse them to disguise a poster as homework or a test.
- See [event contract](event-records-contract.md). It explicitly labels student publication as a
  proposal requiring product decisions. Dates remain `when_text`; no full location/lifecycle yet.
- Timetable writes and audit already exist in `headman_schedule.py` / `schedule_audit.py`.
  C+ has no structured image-to-schedule suggestion adapter or confirmation lifecycle.
  Official timetable writes must stay in the authorized confirmation flow.
- Text classification already distinguishes homework, checks, events, organisational information
  and candidates. Its full replacement is unnecessary. Correct model handling of in-class work,
  ambiguity and mixed image entities has NOT been measured by returning predetermined fake JSON.

## Offline evidence and quality baseline

`tests/test_image_analysis_baseline.py` adds 10 observational checks. They pass by reproducing
current defects and are explicitly NOT new-behaviour acceptance tests. Convert each observation
to the intended invariant as the responsible implementation stage lands; never weaken an existing
acceptance test to make a fix pass.

`tests/fixtures/ai_analysis/image-scenarios.json` records 11 entirely synthetic scenarios and
expected facts/actions: crops, pages, classwork, homework, ambiguity, timetable, poster, personal
invitation, mixed entities, correction, unreadable input. These are text annotations for future
image fixtures, not actual labelled pixel/model outputs. No private chat data was copied.

Measured stub baseline: image attempt coverage is 1/1, 6/6, 6/7 and 6/12 on the first completed
package with backfill unset. Three-crop semantic input retains 1/3 synthetic readings. Four
similar-format pages retain 1/4 attachments. Those ratios describe these deterministic cases only;
they are NOT population recall, precision or actual vision quality estimates.

Using the repository's accounting formula, a fake successful vision answer with 900 input and
60 output tokens costs USD 0.000342. Two text stubs together cost USD 0.000018. The retry probe
performs two successful readings but records only USD 0.000360 instead of USD 0.000702. This is
an accounting regression, not verification of current provider prices. Actual expenditure: USD 0;
real latency and quality, comparative precision/recall and provider cost remain unmeasured.

## Checks and stage verdict

- Initial focused suite: **153 passed**, 0 failed; 23.10 s.
- Initial full suite without PostgreSQL URL: **1078 passed, 26 skipped**, 0 failed; 100.38 s.
- New baseline checks: **10 passed**, 0 failed; 2.64 s.
- Focused real PostgreSQL worker/pipeline/student API: **11 passed**, 0 failed; 6.99 s.
- Full suite including PostgreSQL and new checks: **1114 passed, 0 failed, 0 skipped**; 112.69 s.
  Command: `POSTGRES_TEST_URL=<disposable-local-db> uv run pytest -q`.
- `uv run ruff check .` and `git diff --check`: passed after all additions.
- Disposable PostgreSQL 17, localhost-only port 55439: migration upgrade `base -> 0039`,
  downgrade `0039 -> base`, second upgrade `base -> 0039` all succeeded. No schema edits.

Stage verdict: **PASS for the offline technical audit**. Owner accepted stage 0 and authorized implementation on 2026-10-26. Product image quality is not accepted by this audit; model
classification and pixel-reading accuracy are unmeasured. At the stage-0 checkpoint no implementation fix had shipped.
No paid model calls were made; no deployment or writer-mode switch was performed.

## Current authorization

The owner authorized technical implementation on 2026-10-26 and the synthetic-only paid test
on 2026-10-27, within a single USD 2 cap and the existing provider/model. The approved event
retention and editor-confirmation rules are implemented. Real-group data, real-group shadow,
production deployment, writer changes, history reprocessing and public activation remain
unapproved. Current measurements and the pilot proposal are at the top of this report.

## Implementation stage log

Owner accepted stage 0 and authorized sequential offline implementation on 2026-10-26.
The temporary publication policy in ADR-013 is now approved. Remaining product decisions are
collected at the end of this living report; no per-stage owner approval is required.

### 1a — PASS

Added strict internal image evidence/reading contracts, per-pass jobs and per-attempt ledger
(`0040` additive migration). Public APIs and old C+ snapshots are unchanged. Reviewed uniqueness,
foreign-key retention, pass identity, strict parsing and locking. 13 contract/PostgreSQL checks
passed; migration upgrade/downgrade to 0039/re-upgrade passed. Ruff clean after fixing a test
literal. Actual AI cost USD 0. Next: job scheduling and full image coverage.

### 1b — PASS

Added an independent, allowlisted image queue, disabled by default; images/documents and every
album member receive durable jobs. Six is no longer the new path's completion boundary. Legacy
photo behaviour remains available only when the new feature is off. Streamed Telegram downloads
retain sanitized temporary/terminal errors and reject path escapes/oversized files. Three bounded
attempts, source-revision invalidation, restart reuse and committed pre-call reservations added.
Review covered retention, group isolation, lock order and ambiguous external failures. 65
worker/photo regressions and 28 image/ingestion/contract/PostgreSQL checks passed; lint/diff clean.
Actual AI cost USD 0. The compatibility reader is still one pass in this commit; next is 2a.

### 2a — PASS

Added strict structured reading with text/blocks/table rows/facts/uncertainty, multi-label semantics,
schedule rows and event fields. Both versioned visual prompts send the original pixels; B also
receives saved A and group-local messages/replies/roles/schedule/existing-card context. No string
slicing; invalid/truncated responses explicitly fail with usage preserved. Pass B cannot silently
drop A's facts. Each pass commits independently. 20 image contract/worker/real PostgreSQL checks
passed; reviewed prompt injection boundaries, field constraints and feedback from generated cards.
Existing-card changes do not self-trigger unlimited image reanalysis. Real OCR/classification
quality remains UNMEASURED. Actual AI cost USD 0. Next: accounting and recovery hardening.

### 2b — PASS

Shared group/day and rolling-hour accounting now includes both image and text reservations under
the existing group settings lock. Added per-image and per-album/message batch caps, conservative
ambiguous-call settlement, usage/latency per attempt and stale-context/pixel guards. A superseded
pass still settles its real usage. Pass B retries reuse committed A. Expired paid leases stop for
review instead of automatically paying twice. 51 image/shadow/PostgreSQL checks passed, including
zero budget, all caps, crash/lease recovery, edit during paid call and preserved costs across a
B retry. Ruff/diff clean. Reviewed transaction lock order and data isolation. No exact-once
external billing claim: a process crash can leave an explicitly unknown paid outcome. USD 0 spent.

### 3 — PASS

C+ receives typed evidence for every image (source/attachment/revision/fact index); live and shadow
use the same loader. Confirmed visual facts support card/date validation without rewriting raw
source text. Added strict image fact-reference checks and guards against classwork/unknown/poster/
schedule becoming homework; independent text remains eligible when an image fails. Unfinished
sources use explicit image_pending state and reopen when new evidence arrives. Updated extraction
prompt to retain precise confirmed numbers and conditions. 129 initial regressions and 111 final
semantic/hybrid/stored-result/PostgreSQL checks passed. Fixed a test DTO serialization mistake
and the adapter's old fact-path whitelist, then reran successfully. Review covered evidence
scope, retry review-candidates, prompt compatibility and manual source preservation. USD 0 spent.

### 4 — PASS

Added local bounded Pillow visual hashing plus exact pixel hashes. A shared display-only selector
uses visual proof and identical structured content; different facts/pages/crops stay visible.
Explicit card image references exclude unrelated images from the same source. No fixed image
count. The analysis snapshot still contains every image, including hidden exact duplicates.
Live/shadow snapshots carry display metadata; owner preview and student API share selection.
Structured images never replace the full card description. Compatibility notes updated; no
frontend edits or public schema removal. Reviewed per-card source references and image-bomb
handling. 53 initial and 34 display/preview/student API/PostgreSQL checks passed; lint/diff clean.
Selection is deliberately conservative when visual equivalence cannot be proved; real-world
suppression quality remains UNMEASURED. Actual AI cost USD 0.

### 5a — PASS (technical implementation; pilot/UI remain gated)

Added image_schedule_suggestions (`0041`) with source job, official schedule version, typed rows,
revision and retention. Staff-only gated list/confirmation endpoints recheck role, source/context,
period, week anchor and current timetable version. New rows and exact slot updates use existing
manual decisions/audit/deadline rebuild; explicit occurrence mappings use existing once/recurring
change logic and headman locks. Retry confirmation is idempotent. Missing dates, ambiguous lesson
mapping and unmapped subgroups are blocked rather than guessed. Added draft contract in
`packages/contracts/image-findings.openapi.yaml`; no frontend changed. Reviewed role revocation,
transaction atomicity and non-automatic writes. 35 focused regressions and final 8 real PostgreSQL/
API cases passed; 0041 downgrade/re-upgrade, lint and diff checks passed. Actual AI cost USD 0.
Owner/UI choice still needed for subgroup mappings; official timetable never changes automatically.

### 5b — PASS (technical implementation; public contract activation BLOCKED)

Added separate group_events, per-source applied/held findings and immutable revisions (`0042`).
Existing EventRecord candidates map compatibly to drafts; image readings provide richer date,
place/audience/conditions. Concurrent duplicate posters deduplicate under a group lock. Model
updates/cancellation require identity/revision; published or manually locked records retain the
incoming change for review. Staff update/cancel and optional explicit publication/list/detail
endpoints use separate default-off flags plus group allowlist. Personal/unknown sources never
publish to the whole group. Retention is bounded by source expiry; periodic cleanup also runs
when feature flags are off. Updated canonical event contract and additive pilot API specification.
Reviewed source isolation, stale sources, manual locks and prevention of shadow-driven changes to
legacy card display (published canonical image provenance is required). Fixed a PostgreSQL test's
expired ORM fixture reference and title-less candidate handling. Final 60 lifecycle/candidate/
PostgreSQL checks passed; 0042 downgrade/re-upgrade, lint/diff clean. Actual AI cost USD 0.
Owner decisions on public API/frontend, retention and subgroup visibility remain; no real publication.

### 6 — PASS

Full offline image -> two visual passes -> C+ -> canonical card -> student API is tested, including
precise confirmed numbers, complete text alongside images, idempotent reruns and shadow isolation.
Added explicit activation start timestamp to prevent implicit historical processing. Current source/
context checks reject stale readings; stored prompt versions participate in job identity. Preserved
legacy snapshot hashes when new optional fields are absent. Kept legacy context metadata JSON-safe.
Oversized semantic input is explicitly held as needs_context with an incident; full paid image
results remain stored, never truncated. Teacher/location/catalog reference data reaches pass B.

The first rerun found two legacy /explain failures after the date rollover: its old-source + future
schedule span exceeded the public 32-day limit. Fixed internal non-overlapping calendar chunking
without weakening that limit. A shadow-isolation assertion now excludes only generated_at, which
changes on every API read. Oversize fixture serialization was corrected to match a real Unicode
JSON answer. All failures were corrected and rechecked; no tests disabled or expectations weakened.
Final full command (backend cwd): `POSTGRES_TEST_URL=<disposable-local-db> uv run pytest -q`:
**1172 passed, 0 failed, 0 skipped**, 150.93 s; one upstream Starlette deprecation warning.
An additional 13 integration/schedule/event checks passed after adding catalog context. PostgreSQL
17 migration cycle in a separate disposable database: base -> 0042 -> base -> 0042 succeeded.
Ruff and diff checks clean. Manual lock, notification, group-isolation and concurrency regressions
are included in the full suite. Actual AI cost USD 0. Next: offline evaluation and rollout preparation.


### 7 — PASS for offline preparation; real vision evaluation BLOCKED

Added offline-only `image_evaluation.py`: render synthetic PNGs, evaluate recorded observations,
compare old/new only for identical pixel + chat/annotation input fingerprints. Missing observations
produce null metrics and UNMEASURED, not fabricated accuracy. Exact normalized fact annotations
and optional adjudicated checks cover facts, purpose, classwork/homework counts, schedule/event
fields, display, linking and unique-fact preservation; semantic paraphrases need adjudication.
Created 16 printed synthetic PNGs for 11 scenarios, with a versioned manifest. They are not a
handwriting/real-world benchmark. Visually inspected the rendered Cyrillic schedule fixture.
Added private feature-gated staff image diagnostics: states, sanitized errors, attempts, versions,
usage/cost/reservations/unknown outcomes and latency; no image bytes or raw model text. Added
activation/shadow/rollback instructions to backend README. All flags remain off, group allowlist
empty and start timestamp unset. Budget exhaustion defers jobs rather than making them irrelevant.
A final PostgreSQL check found a group with no subject catalog; fixed pass-B context to carry null
instead of raising. Nine focused PostgreSQL/evaluation checks passed after this fix.
Final regression command (backend cwd): `POSTGRES_TEST_URL=<disposable-local-db> uv run pytest -q`.
**1178 passed, 0 failed, 0 skipped**, 144.46 s. Ruff and diff checks passed. Both OpenAPI YAML
contracts parsed (31 existing paths, 7 additive pilot paths). The final full run also caught a
legacy test helper selecting superseded bot frames by timestamp. Updated it to match the actual
worker, which never displays superseded rows; added a regression with a deliberately later stale
frame. All 17 headman checks and then the complete suite passed without disabling tests.
Stage commit: `979e5ed`.
Actual paid API calls: 0. Actual AI expenditure: USD 0. No production or public publication actions.

## Утренний инцидент 27.10.2026 — read-only расследование

Период: 07:00:09–07:02:15 Europe/Moscow (04:00:09–04:02:15 UTC). Источник фактов —
Render runtime PACKAGE_RESULT; ключи и приватный текст переписки в отчёт не включены.
В production работает `f95c516` от deployment 24 октября. Новая локальная image-ветка не
развёрнута и не является причиной этого инцидента.

Подтверждено журналами: 11 completed-пакетов, каждый attempt=1, error=null. Сумма targets —
178 включений (уникальность исходных сообщений по этим строкам определить нельзя). Created=1,
updated=5; остальные 5 пакетов не изменили карточки. В создающем пакете rejected=1, причину
нужно читать из сохранённого validation_result_json. Учтённый расход всех пакетов USD 0.073992732;
шести пакетов с изменениями — USD 0.046189872, из них пяти обновлений — USD 0.034033560.
Это значения backend ledger, не независимая сверка списаний у провайдера.

Обнаружен и локально воспроизведён дефект допуска обновлений из одного старого контекста:

1. Первый пакет создаёт карточку из источника 31.
2. Новый target 32 — синтетическое «Спасибо!», без новых учебных сведений.
3. В окне второго пакета остаётся старый источник 31. Модель предлагает update карточки,
   ссылаясь только на 31 и лишь перефразируя описание.
4. Backend принимает такое предложение и сохраняет revision=2 / updated, хотя новая цель 32
   не подтверждает ни одного изменения. Провайдер в этом тесте локальный, реальных AI-вызовов нет.

Причины в коде работающей версии: Pass 2 получает messages, но не target_message_ids;
валидация требует присутствия источника в общем окне, без требования новой целевой причины
обновления. Старые и новые требования могут заново пересказываться; отличие строки считается
видимым изменением и порождает новое уведомление. _existing_items также ограничивает старое
описание 600 символами. Это воспроизводимый дефект, хорошо объясняющий симптом; без stored
input/validation/revision snapshots нельзя утверждать, что каждая из пяти утренних правок имела
именно такой набор источников или была лишней.

Read-only доступ к деталям ограничен: внешняя allowlist PostgreSQL пуста; прямое внешнее
подключение закрывается; internal hostname недоступен локально; SSH отказал publickey.
Разрешения, allowlist, deployment, writer mode и карточки не менялись. Для окончательной
корреляции нужен разрешённый read-only экспорт соответствующих ShadowEvaluationRun (input,
validation, metrics), CanonicalItemRevision и источников за этот интервал. Никаких новых платных
запросов ради расследования запускать не требуется.

Предлагаемое исправление: явно передавать Pass 2 текущие target IDs; перед применением update
требовать подтверждённую целевую причину/новый факт и сохранять старую карточку при простом
повторном пересказе. Нельзя ограничиваться скрытием уведомлений или запретом всех повторных
обновлений: настоящие дополнения, переносы, отмены и завершённое новое чтение изображения
должны по-прежнему обновлять существующую карточку. Production-патч не применялся.
