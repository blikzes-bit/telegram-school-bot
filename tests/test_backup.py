"""
Stage: export, import and backup.

Covers the export shapes (JSON / CSV / ICS), what must never be in them, the
validator (schema version, broken JSON, oversized file, structure, types,
limits), both import modes, the all-or-nothing transaction, the admin gate and
chat isolation, plus a full export → import round trip.
"""
import datetime
import io
import json
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

import services.backup as backup
from database.db import (
    add_extra_activity, add_homework, add_homework_attachment, get_all_schedule,
    get_extra_activities, get_homework, get_homework_attachments,
    get_lesson_slots, get_or_create_chat, get_chat, import_chat_data,
    save_lesson_slots, save_schedule_day, set_chat_timezone, set_day_override,
    set_lesson_override, set_quiet_hours, update_chat_reminder_times,
)
from handlers.data_backup import (
    BackupStates, apply_backup, export_backup, export_csv, export_ics,
    open_backup_menu, preview_mode, receive_backup_file, receive_wrong_content,
)

CHAT_ID = 880001
OTHER_CHAT_ID = 880002
GROUP_ID = -1008800100
ADMIN_ID = 501
MEMBER_ID = 502

pytestmark = pytest.mark.asyncio


# --- Fakes ------------------------------------------------------------------

class FakeBot:
    def __init__(self, admins=None, files=None, fail_download=False):
        self.admins = admins or set()
        self.files = dict(files or {})
        self.fail_download = fail_download

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(
            status="administrator" if user_id in self.admins else "member"
        )

    async def download(self, file_id, destination=None):
        if self.fail_download:
            raise RuntimeError("network")
        data = self.files[file_id]
        target = destination if destination is not None else io.BytesIO()
        target.write(data)
        target.seek(0)
        return target


class FakeMessage:
    def __init__(self, chat_id=CHAT_ID, chat_type="private", user_id=ADMIN_ID,
                 bot=None, document=None, text=None):
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, full_name="Аня", first_name="Аня")
        self.bot = bot or FakeBot({ADMIN_ID})
        self.document = document
        self.text = text
        self.answers = []
        self.documents_sent = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def answer_document(self, file, **kwargs):
        self.documents_sent.append((file, kwargs))

    @property
    def texts(self):
        return [text for text, _ in self.answers]


class FakeCallback:
    def __init__(self, data, chat_id=CHAT_ID, chat_type="private",
                 user_id=ADMIN_ID, bot=None):
        bot = bot or FakeBot({ADMIN_ID})
        self.message = FakeMessage(chat_id, chat_type, user_id, bot=bot)
        self.data = data
        self.from_user = self.message.from_user
        self.bot = bot
        self.alerts = []
        self.notices = []

    async def answer(self, *args, **kwargs):
        text = args[0] if args else kwargs.get("text")
        (self.alerts if kwargs.get("show_alert") else self.notices).append(text)


def _state(chat_id=CHAT_ID):
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


def _doc(file_id="fid-1", size=None):
    return SimpleNamespace(file_id=file_id, file_size=size, file_name="backup.json")


def _content(payload):
    """Backup contents minus the wall-clock ``exported_at`` header, so two
    snapshots taken at different instants can be compared for real changes."""
    return {k: v for k, v in payload.items() if k != "exported_at"}


# --- Fixtures ---------------------------------------------------------------

async def _seed(chat_id=CHAT_ID, chat_type="private"):
    """A chat with a bit of everything, so a round trip has something to prove."""
    await get_or_create_chat(chat_id, chat_type)
    await save_lesson_slots(chat_id, [(1, "08:30", "09:15"), (2, "09:25", "10:10")])
    await save_schedule_day(chat_id, 0, [(1, "Математика"), (2, "Физика")])
    await save_schedule_day(chat_id, 1, [(1, "История")], week_type="A")
    await update_chat_reminder_times(chat_id, hw_time="17:45", schedule_time="20:15")
    await set_quiet_hours(chat_id, "22:00", "07:00")
    await set_chat_timezone(chat_id, "Europe/Warsaw")
    await set_day_override(chat_id, datetime.date(2026, 9, 1), "holiday", note="1 сентября")
    await set_lesson_override(
        chat_id, datetime.date(2026, 9, 2), 1, "cancel", note="учитель болен"
    )
    await add_extra_activity(
        chat_id, "Английский", "weekly", "18:00", day_of_week=2,
        end_time="19:00", location="каб. 5", actor_user_id=ADMIN_ID, actor_name="Аня",
    )
    hw = await add_homework(
        chat_id, "Математика", datetime.date(2026, 9, 3), "стр. 15",
        actor_user_id=ADMIN_ID, actor_name="Аня",
    )
    await add_homework_attachment(
        chat_id, hw.id, "file-abc", "uniq-abc", "photo",
        file_name="scan.jpg", file_size=1234, caption="условие",
        actor_user_id=ADMIN_ID, actor_name="Аня",
    )
    return hw


# --- Export -----------------------------------------------------------------

async def test_backup_contains_everything(db):
    await _seed()
    payload = await backup.build_backup(CHAT_ID)

    assert payload["schema_version"] == backup.SCHEMA_VERSION
    assert payload["chat"]["timezone"] == "Europe/Warsaw"
    assert payload["chat"]["hw_reminder_time"] == "17:45"
    assert payload["chat"]["quiet_start"] == "22:00"
    assert len(payload["lesson_slots"]) == 2
    # Both the 'all' template and the week-A template are exported.
    assert {row["week_type"] for row in payload["schedule"]} == {"all", "A"}
    assert payload["day_overrides"][0]["day_type"] == "holiday"
    assert payload["lesson_overrides"][0]["action"] == "cancel"
    assert payload["extra_activities"][0]["title"] == "Английский"
    assert payload["homework"][0]["created_by_name"] == "Аня"
    attachment = payload["homework"][0]["attachments"][0]
    assert attachment["file_id"] == "file-abc"
    assert attachment["file_name"] == "scan.jpg"


async def test_backup_excludes_secrets_and_service_data(db):
    await _seed()
    text = backup.dump_json(await backup.build_backup(CHAT_ID)).decode("utf-8")

    for forbidden in ("BOT_TOKEN", "DATABASE_URL", "chunks_json", "fsm", "is_blocked",
                      "last_hw_reminder_date", "file_path"):
        assert forbidden not in text
    # The attachment carries a reference and metadata — never bytes.
    assert "file_unique_id" in text
    assert "base64" not in text


async def test_backup_of_unknown_chat_is_empty_but_valid(db):
    payload = await backup.build_backup(999999)
    assert payload["chat"] == {}
    assert payload["homework"] == []
    # Still a parseable backup (an empty chat is a legitimate thing to restore).
    assert backup.parse_backup(backup.dump_json(payload))["total_rows"] == 0


async def test_audit_export_is_separate_and_not_importable(db):
    await _seed()
    payload = await backup.build_audit_export(CHAT_ID)
    assert payload["kind"] == "audit_log"
    assert isinstance(payload["audit_log"], list)

    with pytest.raises(backup.BackupError) as exc:
        backup.parse_backup(backup.dump_json(payload))
    assert "истории" in str(exc.value)


async def test_schedule_csv(db):
    await _seed()
    data = await backup.schedule_csv(CHAT_ID)
    assert data.startswith(b"\xef\xbb\xbf")  # BOM, so Excel reads UTF-8
    text = data.decode("utf-8-sig")
    assert "Неделя;День;Урок;Начало;Конец;Предмет" in text
    assert "Понедельник;1;08:30;09:15;Математика" in text
    assert "A (нечётная);Вторник;1;08:30;09:15;История" in text


async def test_calendar_ics(db):
    await _seed()
    today = datetime.date(2026, 8, 31)  # a Monday
    text = (await backup.calendar_ics(CHAT_ID, today, days_ahead=7)).decode("utf-8")

    assert text.startswith("BEGIN:VCALENDAR")
    assert text.rstrip().endswith("END:VCALENDAR")
    assert "SUMMARY:Математика" in text
    assert "SUMMARY:🎯 Английский" in text
    assert "LOCATION:каб. 5" in text
    # Homework deadline as an all-day event.
    assert "DTSTART;VALUE=DATE:20260903" in text
    # 1 September is a holiday for this chat → no lessons that day. 08:30 in
    # Europe/Warsaw is 06:30 UTC.
    assert "DTSTART:20260901T063000Z" not in text
    assert "DTSTART:20260831T063000Z" in text
    # The cancelled lesson on 2 September is not in the calendar.
    assert "DTSTART:20260902T063000Z" not in text


async def test_ics_escapes_and_folds_long_values(db):
    await get_or_create_chat(CHAT_ID, "private")
    await save_lesson_slots(CHAT_ID, [(1, "08:00", "08:45")])
    await save_schedule_day(CHAT_ID, 0, [(1, "Химия; опыты, дома")])
    text = (await backup.calendar_ics(CHAT_ID, datetime.date(2026, 8, 31), days_ahead=1)).decode("utf-8")
    assert "SUMMARY:Химия\\; опыты\\, дома" in text

    long_line = "X" * 200
    folded = backup._ics_fold(f"SUMMARY:{long_line}")
    assert "\r\n " in folded
    assert all(len(part.encode("utf-8")) <= 75 for part in folded.split("\r\n"))
    # Cyrillic must never be cut mid-character.
    cyr = backup._ics_fold("SUMMARY:" + "Щ" * 100)
    assert "".join(part.lstrip(" ") if i else part
                   for i, part in enumerate(cyr.split("\r\n"))) == "SUMMARY:" + "Щ" * 100


# --- Validation -------------------------------------------------------------

def _minimal(**extra):
    payload = {"schema_version": backup.SCHEMA_VERSION}
    payload.update(extra)
    return json.dumps(payload).encode("utf-8")


async def test_rejects_broken_json():
    with pytest.raises(backup.BackupError) as exc:
        backup.parse_backup(b'{"schema_version": 1, "homework": [')
    assert "JSON" in str(exc.value)


async def test_rejects_empty_and_non_utf8():
    with pytest.raises(backup.BackupError):
        backup.parse_backup(b"   ")
    with pytest.raises(backup.BackupError) as exc:
        backup.parse_backup(b"\xff\xfe\x00garbage")
    assert "UTF-8" in str(exc.value)


async def test_rejects_old_and_future_schema_version():
    for version in (0, 99):
        with pytest.raises(backup.BackupError) as exc:
            backup.parse_backup(_minimal_version(version))
        assert "формата" in str(exc.value)

    with pytest.raises(backup.BackupError) as exc:
        backup.parse_backup(b'{"homework": []}')
    assert "schema_version" in str(exc.value)


def _minimal_version(version):
    return json.dumps({"schema_version": version}).encode("utf-8")


async def test_rejects_oversized_file():
    oversized = b'{"schema_version": 1, "pad": "' + b"x" * backup.MAX_BACKUP_BYTES + b'"}'
    with pytest.raises(backup.BackupError) as exc:
        backup.parse_backup(oversized)
    assert "слишком большой" in str(exc.value)


async def test_rejects_too_many_rows():
    too_many = backup.MAX_ROWS["homework"] + 1
    rows = [
        {"subject_name": "П", "due_date": "2026-01-01", "description": str(i)}
        for i in range(too_many)
    ]
    with pytest.raises(backup.BackupError) as exc:
        backup.parse_backup(_minimal(homework=rows))
    assert str(too_many) in str(exc.value)


async def test_rejects_bad_types_and_ranges():
    cases = [
        {"homework": {"not": "a list"}},
        {"homework": [{"subject_name": 5, "due_date": "2026-01-01", "description": "x"}]},
        {"homework": [{"subject_name": "П", "due_date": "31.12.2026", "description": "x"}]},
        {"homework": [{"subject_name": "П", "due_date": "2026-01-01"}]},
        {"schedule": [{"day_of_week": 9, "lesson_number": 1, "subject_name": "П"}]},
        {"schedule": [{"day_of_week": True, "lesson_number": 1, "subject_name": "П"}]},
        {"schedule": [{"day_of_week": 0, "lesson_number": 1, "subject_name": "П",
                       "week_type": "Z"}]},
        {"lesson_slots": [{"lesson_number": 1, "start_time": "9:99", "end_time": "10:00"}]},
        {"lesson_slots": [{"lesson_number": 1, "start_time": "10:00", "end_time": "09:00"}]},
        {"day_overrides": [{"date": "2026-01-01", "day_type": "party"}]},
        {"lesson_overrides": [{"date": "2026-01-01", "lesson_number": 1, "action": "drop"}]},
        {"extra_activities": [{"title": "К", "kind": "weekly", "start_time": "18:00"}]},
        {"extra_activities": [{"title": "К", "kind": "once", "start_time": "18:00",
                               "day_of_week": 1, "activity_date": "2026-01-01"}]},
        {"chat": {"timezone": "Mars/Olympus"}},
        {"chat": {"hw_edit_policy": "everyone"}},
        {"chat": {"week_anchor_monday": "2026-09-02"}},  # a Tuesday
    ]
    for case in cases:
        with pytest.raises(backup.BackupError):
            backup.parse_backup(_minimal(**case))


async def test_rejects_long_strings_and_too_many_attachments():
    with pytest.raises(backup.BackupError):
        backup.parse_backup(_minimal(homework=[{
            "subject_name": "П" * 500, "due_date": "2026-01-01", "description": "x",
        }]))
    with pytest.raises(backup.BackupError) as exc:
        backup.parse_backup(_minimal(homework=[{
            "subject_name": "П", "due_date": "2026-01-01", "description": "x",
            "attachments": [
                {"file_id": f"f{i}", "file_unique_id": f"u{i}", "file_type": "photo"}
                for i in range(backup.MAX_ATTACHMENTS_PER_HOMEWORK + 1)
            ],
        }]))
    assert "вложений" in str(exc.value)


async def test_duplicate_keys_are_rejected():
    with pytest.raises(backup.BackupError):
        backup.parse_backup(_minimal(lesson_slots=[
            {"lesson_number": 1, "start_time": "08:00", "end_time": "08:45"},
            {"lesson_number": 1, "start_time": "09:00", "end_time": "09:45"},
        ]))
    with pytest.raises(backup.BackupError):
        backup.parse_backup(_minimal(day_overrides=[
            {"date": "2026-01-01", "day_type": "free"},
            {"date": "2026-01-01", "day_type": "holiday"},
        ]))


async def test_unknown_keys_are_ignored_not_fatal():
    payload = backup.parse_backup(_minimal(
        future_section=[1, 2, 3],
        chat={"timezone": "UTC", "some_new_setting": 42},
        homework=[{"subject_name": "П", "due_date": "2026-01-01",
                   "description": "x", "unknown": "ignored"}],
    ))
    assert payload["chat"] == {"timezone": "UTC"}
    assert "unknown" not in payload["homework"][0]
    assert "future_section" not in payload


async def test_attachment_file_name_is_sanitised():
    payload = backup.parse_backup(_minimal(homework=[{
        "subject_name": "П", "due_date": "2026-01-01", "description": "x",
        "attachments": [{
            "file_id": "f", "file_unique_id": "u", "file_type": "document",
            "file_name": "../../etc/passwd",
        }],
    }]))
    assert payload["homework"][0]["attachments"][0]["file_name"] == "passwd"


async def test_audit_rows_in_a_backup_are_counted_but_not_imported(db):
    payload = backup.parse_backup(_minimal(
        audit_log=[{"action": "create", "entity_type": "homework"}],
    ))
    assert payload["audit_skipped"] == 1
    await get_or_create_chat(CHAT_ID, "private")
    await backup.apply_import(CHAT_ID, payload, backup.IMPORT_MODE_MERGE)
    # Nothing was written into the journal by the import itself.
    from database.db import count_audit_logs
    assert await count_audit_logs(CHAT_ID) == 0


# --- Round trip -------------------------------------------------------------

async def test_round_trip_export_import(db):
    await _seed()
    exported = backup.dump_json(await backup.build_backup(CHAT_ID))

    payload = backup.parse_backup(exported)
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    await backup.apply_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_REPLACE)

    restored = await backup.build_backup(OTHER_CHAT_ID)
    original = json.loads(exported)
    for section in ("chat", "lesson_slots", "schedule", "day_overrides",
                    "lesson_overrides", "extra_activities", "homework"):
        assert restored[section] == original[section], section


async def test_round_trip_preserves_authorship_and_attachments(db):
    await _seed()
    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    await backup.apply_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_MERGE)

    hw = (await get_homework(OTHER_CHAT_ID))[0]
    assert hw.created_by_user_id == ADMIN_ID
    assert hw.created_by_name == "Аня"
    attachments = await get_homework_attachments(OTHER_CHAT_ID, hw.id)
    assert [a.file_unique_id for a in attachments] == ["uniq-abc"]


# --- Import modes -----------------------------------------------------------

async def test_merge_keeps_existing_and_adds_missing(db):
    await _seed()
    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))

    await get_or_create_chat(OTHER_CHAT_ID, "private")
    kept = await add_homework(
        OTHER_CHAT_ID, "Биология", datetime.date(2026, 10, 10), "своё"
    )
    await save_lesson_slots(OTHER_CHAT_ID, [(1, "07:00", "07:45")])

    counts = await backup.apply_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_MERGE)

    subjects = {hw.subject_name for hw in await get_homework(OTHER_CHAT_ID)}
    assert subjects == {"Биология", "Математика"}
    assert (await get_homework_by_id_subject(OTHER_CHAT_ID, kept.id)) == "Биология"
    # Lesson 1 existed → updated in place, lesson 2 → created.
    slots = {slot.lesson_number: slot.start_time for slot in await get_lesson_slots(OTHER_CHAT_ID)}
    assert slots == {1: "08:30", 2: "09:25"}
    assert counts["slots_updated"] == 1
    assert counts["slots_created"] == 1
    assert "deleted" not in counts


async def get_homework_by_id_subject(chat_id, hw_id):
    for hw in await get_homework(chat_id):
        if hw.id == hw_id:
            return hw.subject_name
    return None


async def test_merge_skips_identical_rows(db):
    await _seed()
    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))

    counts = await backup.apply_import(CHAT_ID, payload, backup.IMPORT_MODE_MERGE)
    assert counts["homework_skipped"] == 1
    assert counts["extra_skipped"] == 1
    assert counts.get("homework_created", 0) == 0
    # Importing a chat's own backup into itself changes nothing.
    assert len(await get_homework(CHAT_ID)) == 1
    assert len(await get_extra_activities(CHAT_ID)) == 1


async def test_replace_wipes_then_writes(db):
    await _seed()
    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))

    await get_or_create_chat(OTHER_CHAT_ID, "private")
    await add_homework(OTHER_CHAT_ID, "Биология", datetime.date(2026, 10, 10), "своё")
    await add_extra_activity(OTHER_CHAT_ID, "Шахматы", "weekly", "17:00", day_of_week=4)
    await save_lesson_slots(OTHER_CHAT_ID, [(1, "07:00", "07:45")])

    counts = await backup.apply_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_REPLACE)

    assert counts["deleted"] >= 3
    assert {hw.subject_name for hw in await get_homework(OTHER_CHAT_ID)} == {"Математика"}
    assert {a.title for a in await get_extra_activities(OTHER_CHAT_ID)} == {"Английский"}
    slots = {slot.lesson_number for slot in await get_lesson_slots(OTHER_CHAT_ID)}
    assert slots == {1, 2}


async def test_import_applies_settings_and_timezone(db):
    await _seed()
    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))
    await get_or_create_chat(OTHER_CHAT_ID, "private")

    await backup.apply_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_MERGE)

    chat = await get_chat(OTHER_CHAT_ID)
    assert chat.timezone == "Europe/Warsaw"
    assert chat.hw_reminder_time == "17:45"
    assert chat.quiet_start == "22:00"


async def test_import_cannot_change_identity_or_delivery_state(db):
    """A crafted file must not be able to redirect itself or replay reminders."""
    await get_or_create_chat(CHAT_ID, "group")
    raw = json.dumps({
        "schema_version": 1,
        "source_chat_id": OTHER_CHAT_ID,
        "chat": {
            "timezone": "UTC",
            "is_blocked": True,
            "chat_id": OTHER_CHAT_ID,
            "chat_type": "private",
            "last_hw_reminder_date": "2026-01-01",
            "is_onboarded": False,
        },
    }).encode("utf-8")

    payload = backup.parse_backup(raw)
    assert set(payload["chat"]) == {"timezone"}

    await backup.apply_import(CHAT_ID, payload, backup.IMPORT_MODE_MERGE, chat_type="group")
    chat = await get_chat(CHAT_ID)
    assert chat.chat_id == CHAT_ID
    assert chat.chat_type == "group"
    assert chat.is_blocked is False
    assert chat.last_hw_reminder_date is None
    # The other chat was never created/touched.
    assert await get_chat(OTHER_CHAT_ID) is None


async def test_import_does_not_touch_another_chat(db):
    await _seed(CHAT_ID)
    await _seed(OTHER_CHAT_ID)
    before = await backup.build_backup(OTHER_CHAT_ID)

    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))
    await backup.apply_import(CHAT_ID, payload, backup.IMPORT_MODE_REPLACE)

    assert _content(await backup.build_backup(OTHER_CHAT_ID)) == _content(before)


async def test_unknown_mode_is_rejected(db):
    await get_or_create_chat(CHAT_ID, "private")
    payload = backup.parse_backup(_minimal())
    with pytest.raises(backup.BackupError):
        await backup.apply_import(CHAT_ID, payload, "wipe")
    with pytest.raises(ValueError):
        await import_chat_data(CHAT_ID, payload, "wipe")


# --- Rollback ---------------------------------------------------------------

async def test_failed_import_rolls_everything_back(db):
    """
    A row that slips past validation (here: an invalid ``week_type`` fed straight
    to the DB) must abort the whole transaction — including the ``replace``
    deletes that ran first.
    """
    await _seed()
    before = await backup.build_backup(CHAT_ID)

    bad_payload = {
        "chat": {"timezone": "UTC"},
        "lesson_slots": [{"lesson_number": 5, "start_time": "12:00", "end_time": "12:45"}],
        "homework": [{"subject_name": "Новое", "due_date": datetime.date(2026, 12, 1),
                      "description": "должно исчезнуть", "is_completed": False}],
        "schedule": [{"week_type": "Z", "day_of_week": 0, "lesson_number": 1,
                      "subject_name": "плохая строка"}],
    }

    with pytest.raises(Exception):
        await import_chat_data(CHAT_ID, bad_payload, "replace")

    assert _content(await backup.build_backup(CHAT_ID)) == _content(before)
    assert {hw.subject_name for hw in await get_homework(CHAT_ID)} == {"Математика"}
    assert len(await get_all_schedule(CHAT_ID)) == len(before["schedule"])
    assert (await get_chat(CHAT_ID)).timezone == "Europe/Warsaw"


# --- Preview ----------------------------------------------------------------

async def test_preview_reports_counts_without_writing(db):
    await _seed()
    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    await add_homework(OTHER_CHAT_ID, "Биология", datetime.date(2026, 10, 10), "своё")

    merge = await backup.preview_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_MERGE)
    replace = await backup.preview_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_REPLACE)

    assert merge["deleted"] == 0
    assert merge["created"] > 0
    assert replace["deleted"] == 1  # the one existing homework row
    assert replace["created"] == merge["created"]
    # Nothing was written by either preview.
    assert len(await get_homework(OTHER_CHAT_ID)) == 1


async def test_preview_matches_apply(db):
    await _seed()
    payload = backup.parse_backup(backup.dump_json(await backup.build_backup(CHAT_ID)))
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    await save_lesson_slots(OTHER_CHAT_ID, [(1, "07:00", "07:45")])

    preview = await backup.preview_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_MERGE)
    counts = await backup.apply_import(OTHER_CHAT_ID, payload, backup.IMPORT_MODE_MERGE)

    created = sum(v for k, v in counts.items() if k.endswith("_created")
                  and k != "attachments_created")
    updated = sum(v for k, v in counts.items() if k.endswith("_updated")
                  and k != "settings_updated")
    assert (created, updated) == (preview["created"], preview["updated"])


async def test_target_chat_note_only_for_foreign_files():
    assert backup.target_chat_note({"source_chat_id": CHAT_ID}, CHAT_ID) is None
    assert backup.target_chat_note({}, CHAT_ID) is None
    note = backup.target_chat_note({"source_chat_id": OTHER_CHAT_ID}, CHAT_ID)
    assert note and "только в этот чат" in note


# --- Handlers: access control and flow --------------------------------------

async def test_section_is_admin_only_in_a_group(db):
    await get_or_create_chat(GROUP_ID, "supergroup")
    for handler in (open_backup_menu, export_backup, export_csv, export_ics):
        callback = FakeCallback("bk_menu", chat_id=GROUP_ID, chat_type="supergroup",
                                user_id=MEMBER_ID, bot=FakeBot({ADMIN_ID}))
        await handler(callback, _state(GROUP_ID))
        assert callback.alerts, handler.__name__
        assert callback.message.documents_sent == []


async def test_admin_gets_the_backup_document(db):
    await _seed(GROUP_ID, "supergroup")
    callback = FakeCallback("bk_json", chat_id=GROUP_ID, chat_type="supergroup",
                            user_id=ADMIN_ID, bot=FakeBot({ADMIN_ID}))
    await export_backup(callback, _state(GROUP_ID))

    assert len(callback.message.documents_sent) == 1
    document, kwargs = callback.message.documents_sent[0]
    assert document.filename.endswith(".json")
    assert json.loads(document.data.decode("utf-8"))["schema_version"] == 1
    assert "schema_version" in kwargs["caption"]


async def test_import_requires_admin_in_a_group(db):
    await get_or_create_chat(GROUP_ID, "supergroup")
    message = FakeMessage(GROUP_ID, "supergroup", MEMBER_ID,
                          bot=FakeBot({ADMIN_ID}, {"fid-1": b"{}"}),
                          document=_doc())
    state = _state(GROUP_ID)
    await state.set_state(BackupStates.waiting_for_file)
    await receive_backup_file(message, state)

    assert any("администратор" in text for text in message.texts)
    assert (await state.get_data()).get("bk_file_id") is None


async def test_oversized_document_is_refused_before_download(db):
    await get_or_create_chat(CHAT_ID, "private")
    bot = FakeBot({ADMIN_ID}, files={})  # no file registered: a download would KeyError
    message = FakeMessage(bot=bot, document=_doc(size=backup.MAX_BACKUP_BYTES + 1))
    await receive_backup_file(message, _state())
    assert any("слишком большой" in text for text in message.texts)


async def test_non_document_upload_is_explained(db):
    message = FakeMessage(text="вот мои данные")
    await receive_wrong_content(message)
    assert any("файл" in text for text in message.texts)


async def test_broken_file_upload_keeps_the_flow_usable(db):
    await get_or_create_chat(CHAT_ID, "private")
    bot = FakeBot({ADMIN_ID}, {"fid-1": b"{not json"})
    message = FakeMessage(bot=bot, document=_doc())
    state = _state()
    await state.set_state(BackupStates.waiting_for_file)
    await receive_backup_file(message, state)

    assert any("JSON" in text for text in message.texts)
    assert (await state.get_data()).get("bk_file_id") is None


async def test_full_import_flow_merge(db):
    await _seed()
    exported = backup.dump_json(await backup.build_backup(CHAT_ID))
    bot = FakeBot({ADMIN_ID}, {"fid-1": exported})

    await get_or_create_chat(OTHER_CHAT_ID, "private")
    state = _state(OTHER_CHAT_ID)
    await state.set_state(BackupStates.waiting_for_file)

    message = FakeMessage(OTHER_CHAT_ID, bot=bot, document=_doc())
    await receive_backup_file(message, state)
    assert (await state.get_data())["bk_file_id"] == "fid-1"

    callback = FakeCallback("bk_mode:merge", chat_id=OTHER_CHAT_ID, bot=bot)
    await preview_mode(callback, state)
    assert any("Дополнить" in text for text in callback.message.texts)

    callback = FakeCallback("bk_apply:merge", chat_id=OTHER_CHAT_ID, bot=bot)
    await apply_backup(callback, state)
    assert any("Восстановление завершено" in text for text in callback.message.texts)
    assert await state.get_state() is None
    assert {hw.subject_name for hw in await get_homework(OTHER_CHAT_ID)} == {"Математика"}


async def test_replace_needs_two_confirmations(db):
    from handlers.data_backup import confirm_replace_again

    await _seed()
    exported = backup.dump_json(await backup.build_backup(CHAT_ID))
    bot = FakeBot({ADMIN_ID}, {"fid-1": exported})
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    await add_homework(OTHER_CHAT_ID, "Биология", datetime.date(2026, 10, 10), "своё")

    state = _state(OTHER_CHAT_ID)
    await state.set_state(BackupStates.waiting_for_file)
    await state.update_data(bk_file_id="fid-1")

    first = FakeCallback("bk_mode:replace", chat_id=OTHER_CHAT_ID, bot=bot)
    await preview_mode(first, state)
    # The first screen only offers "next", never the destructive action.
    assert any("Будет удалено записей" in text for text in first.message.texts)

    second = FakeCallback("bk_replace_ask2", chat_id=OTHER_CHAT_ID, bot=bot)
    await confirm_replace_again(second, state)
    assert any("Последнее предупреждение" in text for text in second.message.texts)
    # Still nothing written after two screens.
    assert len(await get_homework(OTHER_CHAT_ID)) == 1

    third = FakeCallback("bk_apply:replace", chat_id=OTHER_CHAT_ID, bot=bot)
    await apply_backup(third, state)
    assert {hw.subject_name for hw in await get_homework(OTHER_CHAT_ID)} == {"Математика"}


async def test_stale_confirm_button_without_state(db):
    from handlers.data_backup import stale_import_button
    callback = FakeCallback("bk_apply:merge")
    await stale_import_button(callback)
    assert callback.alerts


async def test_apply_without_a_file_in_state_is_reported(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(BackupStates.waiting_for_file)
    callback = FakeCallback("bk_apply:merge")
    await apply_backup(callback, state)
    assert callback.alerts


async def test_unknown_mode_in_callback_is_rejected(db):
    await get_or_create_chat(CHAT_ID, "private")
    state = _state()
    await state.set_state(BackupStates.waiting_for_file)
    await state.update_data(bk_file_id="fid-1")
    callback = FakeCallback("bk_apply:wipe")
    await apply_backup(callback, state)
    assert callback.alerts


async def test_download_failure_is_reported_not_raised(db):
    await get_or_create_chat(CHAT_ID, "private")
    bot = FakeBot({ADMIN_ID}, fail_download=True)
    message = FakeMessage(bot=bot, document=_doc())
    state = _state()
    await state.set_state(BackupStates.waiting_for_file)
    await receive_backup_file(message, state)
    assert any("Telegram" in text for text in message.texts)


async def test_import_failure_is_reported_and_state_cleared(db, monkeypatch):
    await _seed()
    exported = backup.dump_json(await backup.build_backup(CHAT_ID))
    bot = FakeBot({ADMIN_ID}, {"fid-1": exported})

    async def boom(*args, **kwargs):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(backup, "apply_import", boom)

    state = _state()
    await state.set_state(BackupStates.waiting_for_file)
    await state.update_data(bk_file_id="fid-1")
    callback = FakeCallback("bk_apply:merge", bot=bot)
    await apply_backup(callback, state)

    assert any("Импорт не выполнен" in text for text in callback.message.texts)
    # The internal error text is never shown to the user.
    assert not any("db exploded" in text for text in callback.message.texts)
    assert await state.get_state() is None
