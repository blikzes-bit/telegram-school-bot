# Заметки для следующей сессии (вставь вместе с последним промтом)

> Файл рабочий/одноразовый: он не закоммичен и не нужен проекту — можно удалить, когда закончим.
> Дата составления: 2026-07-28.

## 1. Где мы находимся

Проект `telegram_school_bot` (Python 3.12 + aiogram 3 + SQLAlchemy 2/aiosqlite + Alembic + APScheduler).
Пройдены промты по порядку; **последний выполненный — ПРОМПТ 7** (часовой пояс каждого чата).
Следующим идёт ПРОМПТ 8 — его я ещё не видел.

**Важно: коммитов нет.** Всё лежит в рабочем дереве. Последний коммит в `main`:
`cbdbdaa Security/reliability audit: access control, HTML formatting, atomic onboarding, outbox-based reminders`.
Всё, что сделано в промтах 4–7, — незакоммиченные изменения и новые (untracked) файлы.
Каждый промт заканчивался инструкцией «не делай commit», поэтому история не двигалась.

**Состояние проверок на конец сессии (всё зелёное):**

```
pytest       → 376 passed
ruff check . → All checks passed
mypy .       → Success: no issues found in 31 source files
pip-audit -r requirements.txt → No known vulnerabilities found
alembic      → round-trip base → head → base → head проходит
```

Команды для повторного прогона (из корня проекта):

```bash
python -m pytest -q
python -m ruff check .
python -m mypy .
python -m pip_audit -r requirements.txt
```

Проверить, что миграции соответствуют моделям (autogenerate должен быть пустым):

```bash
rm -f .mig_test.db
DATABASE_URL="sqlite+aiosqlite:///.mig_test.db" python -m alembic upgrade head
DATABASE_URL="sqlite+aiosqlite:///.mig_test.db" python -m alembic revision --autogenerate -m "drift"
# посмотреть, что в upgrade() пусто, затем удалить этот файл ревизии и .mig_test.db
```

## 2. Что появилось в промтах 5–7 (краткая карта)

**Этап 5 — авторство, журнал изменений, права на ДЗ**

- `database/models.py`: `AuthorshipMixin` (`created_by_user_id/name`, `updated_by_user_id/name`,
  `created_at`, `updated_at` — строки ISO-8601 UTC) на `Homework`, `ExtraActivity`,
  `DayOverride`, `LessonOverride`; новая модель `AuditLog`; `Chat.hw_edit_policy`.
- `services/audit.py` — актор из события, безопасные краткие описания, запись в журнал.
- `services/permissions.py` — три политики: `collaborative` (по умолчанию) /
  `creator_or_admin` / `admin_only`.
- `handlers/history.py` — раздел «📜 История» (пагинация + фильтр по типу, admin-only в группе).
- Миграция `b3d7f5a91c62_add_authorship_and_audit_log.py`.
- `config.AUDIT_RETENTION_DAYS` (по умолчанию 180, `0` — не удалять);
  чистка в `services/scheduler.prune_audit_log()`.

**Этап 6 — фото и файлы в ДЗ**

- `database/models.HomeworkAttachment` (FK на `homework.id`, `ON DELETE CASCADE`,
  уникальность `(homework_id, file_unique_id)`).
- `services/attachments.py` — разбор входящего сообщения в ссылку на файл + валидация.
- `utils.py` — `safe_file_name`, `format_file_size` и лимиты
  (`MAX_ATTACHMENTS_PER_HOMEWORK = 5`, `MAX_ATTACHMENT_SIZE_BYTES = 50 МБ`,
  `MAX_ATTACHMENT_CAPTION_LEN = 500`).
- Миграция `c9e2b41f7a83_add_homework_attachments.py`.

**Этап 7 — часовой пояс каждого чата**

- `Chat.timezone` (NOT NULL, server-default из `TIMEZONE` → существующие чаты бэкфилятся).
- `services/timeservice.py` полностью переписан: `chat_tz`, `tz_from_name`,
  `normalize_timezone`, `is_valid_timezone`, `tz_for_chat_id`, `today_for_chat_id`,
  `localize`/`combine` (DST), `POPULAR_TIMEZONES`, `tz_label`, `local_time_label`.
- `services/scheduler.py`: класс `ChatClock` — у каждого чата своё `now/today/tomorrow`;
  date-зависимые batch-запросы группируются по различным локальным «завтра».
- Миграция `d1a6c85b3e97_add_per_chat_timezone.py`.

Актуальная цепочка миграций:
`e42f61dd6f2f → 8f4eb80a9671 → c7f3a9b2d1e4 → d5a1c9f42b7e → f1b8e3c26a9d → a2c4e6f80b13 → b3d7f5a91c62 → c9e2b41f7a83 → d1a6c85b3e97 (head)`

## 3. Инварианты проекта — их нельзя нарушать в следующем этапе

1. **Никаких пользовательских дат через глобальный часовой пояс.** `config.TIMEZONE`
   используется **только** внутри `services/timeservice.py` (как дефолт для новых чатов и
   fallback). В хендлерах — `await ts.today_for_chat_id(chat_id)` /
   `await ts.tz_for_chat_id(chat_id)`, в планировщике — `ChatClock`.
   Прямой `pytz.timezone(...)` в хендлерах не заводить: есть `ts.tz_from_name`.
   Служебные метки времени (outbox, аудит, авторство) — всегда UTC ISO (`ts.now_iso_utc()`,
   `audit.now_iso()`).
2. **Проверки прав — на сервере, до обращения к БД.** Скрытая кнопка ничего не защищает.
   Админские действия — `middleware.access.require_admin`; изменение ДЗ — `_guard(...)`
   в `handlers/homework.py` → `services.permissions.require_homework_access`.
   Право на правку ДЗ перепроверяется **и в момент записи**, не только при открытии меню.
3. **Все запросы к БД скоупятся по `chat_id`.** Для вложений — через JOIN на `homework`
   (`get_homework_attachment`, `delete_homework_attachment` и т. д.).
4. **Обратная совместимость данных.** Новые колонки — nullable или с server-default;
   старые записи с `NULL`-авторством — нормальное, поддерживаемое состояние
   (не «дозаполнять» автора при правке). Значения по умолчанию выбираются так, чтобы
   поведение существующих чатов не менялось.
5. **Никаких лишних персональных данных.** В журнал и авторство — только Telegram-id и
   отображаемое имя. Никогда: токены, username, телефон, полный Update, значения
   нового текста ДЗ. Описания в аудите строятся через `audit.summarize(...)` и обрезаются.
6. **Файлы не скачиваются, не распаковываются, не запускаются.** Только `file_id` /
   `file_unique_id` + метаданные. Имя файла — недоверенное, через `utils.safe_file_name`,
   и только для показа.
7. **HTML-экранирование любого пользовательского текста** (`utils.html_escape`) при
   `parse_mode="HTML"` — включая имена авторов и подписи к файлам.
8. **Аудит не должен ломать действие.** `audit.record` глотает свои ошибки и логирует.
   Ночное обслуживание тоже не имеет права уронить тик планировщика.
9. **Идемпотентность напоминаний** — outbox `(chat_id, kind, job_date)` + стемпы
   `last_*_reminder_date`. Дедупликация по календарной дате; именно поэтому повтор часа
   при переходе на зимнее время не даёт второго напоминания. Не заменять это на
   сравнение по минутам.
10. **Каждая схемная правка = новая Alembic-миграция** + запись в `_ensure_column`
    внутри `database.db.init_db` (это только для dev/test-БД). После — проверить, что
    `--autogenerate` даёт пустой diff.
11. **Стиль:** комментарии и docstring'и — на английском, тексты для пользователя — на
    русском. Комментарии объясняют «почему», а не «что». Обновлять README и `/help`
    (`handlers/common.py`) в том же этапе.

## 4. Известные открытые вопросы / принятые решения

- **`creator_or_admin` + запись без автора (`NULL`)** — я решил **разрешать всем участникам**:
  сравнивать не с чем, а запирать класс от собственных старых ДЗ хуже, чем разрешить правку.
  Для строгого варианта есть `admin_only`, где `NULL` тоже ограничен. Оба поведения покрыты
  тестами; если хочешь наоборот — правка в одном месте, `services/permissions.py`.
- **«Экспорт данных» из промта 7 не выполнен, потому что такой функции в проекте нет.**
  Обновлён только перенос `group → supergroup` (`database.db.migrate_chat`) — он теперь
  тащит часовой пояс, режим прав, журнал `AuditLog` и вложения. Если в промте 8 появится
  экспорт — его надо будет писать с нуля, и не забыть про новые поля.
- **Ограничения, зафиксированные в README:** только SQLite; троттлинг отправки — простая
  пауза, а не token-bucket; часть межчатовых запросов планировщика не батчится.

## 5. Что я сделаю завтра, когда ты вставишь промт 8

1. Прочитаю актуальный код (он мог измениться) и прогоню `pytest` + `ruff` + `mypy`
   до начала правок, чтобы иметь чистую базу.
2. Выполню **только** этап из промта 8, соблюдая инварианты из раздела 3.
3. Alembic-миграция, если меняется схема; тесты на всё новое; обновление README и `/help`.
4. Прогоню все проверки (`pytest`, `ruff`, `mypy`, при необходимости `pip-audit`) и
   **не буду делать commit**, если в промте не написано иначе.

Если захочешь наконец закоммитить накопленное (промты 4–7) — скажи, разложу по осмысленным
коммитам вместо одного большого.
