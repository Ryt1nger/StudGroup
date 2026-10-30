# Резервное копирование Free PostgreSQL Render перед закрытым аудитом

## Статус

`studgroup-db` использует Free PostgreSQL Render. На этом compute-плане штатные PITR, Recovery и
logical export отсутствуют. Нельзя считать базу защищённой только потому, что её подключение
закрыто от интернета.

## Обязательный безопасный путь

Перед любым production deployment, который добавляет audit runner или миграцию:

1. Создать логический backup `pg_dump -Fc` от production базы через временный внутренний runner
   либо с явно разрешённого owner-host. URL базы передаётся секретом, не выводится в логи и не
   попадает в Git.
2. Сохранить архив во внешнем защищённом хранилище; файловая система Free Web Service для этого
   непригодна, так как исчезает при restart/sleep.
3. Восстановить архив только в чистый изолированный PostgreSQL-контур тем же major version и
   проверить `pg_restore --list` и наличие схемы/данных. Никогда не выполнять restore поверх
   рабочей базы.
4. Зафиксировать время backup, SHA-256 архива, результат restore и идентификатор исполнителя в
   закрытом operational log без source text, Telegram file_id, URL и credentials.

Пример команд внутри доверенного runner (значения приходят только из secret environment):

```sh
pg_dump --dbname="$DATABASE_URL" --format=custom --no-owner --no-privileges \
  --file="$BACKUP_PATH/studgroup-pre-audit.dump"
sha256sum "$BACKUP_PATH/studgroup-pre-audit.dump"
pg_restore --dbname="$ISOLATED_RESTORE_URL" --clean --if-exists --no-owner --no-privileges \
  "$BACKUP_PATH/studgroup-pre-audit.dump"
pg_restore --list "$BACKUP_PATH/studgroup-pre-audit.dump" >/dev/null
```

## Ограничения текущей инфраструктуры

- Free PostgreSQL допускает только один бесплатный database instance в workspace, поэтому
  изолированный restore не может быть создан вторым бесплатным Render Postgres.
- Free Web Service не поддерживает one-off jobs, shell access или persistent disk.
- Новый платный Render ресурс, платный compute/PITR или внешнее storage не создаются без
  отдельной оценки стоимости и подтверждения владельца.

До подтверждённого backup + restore production deployment и реальный audit runner запрещены.
