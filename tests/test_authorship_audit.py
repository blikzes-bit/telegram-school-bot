"""
Stage: authorship, the audit journal and the homework-edit policy.

Covers:
  * authorship stamped on create/update, and never invented for legacy rows;
  * all three policies (collaborative / creator_or_admin / admin_only) against
    an admin, the author and a different member — enforced server-side, i.e. by
    calling the real handlers, not by inspecting keyboards;
  * legacy entries whose author is NULL;
  * audit entries for create / update / delete / complete / restore, including
    that a deleted record leaves its journal line behind;
  * chat_id isolation of the journal;
  * HTML escaping of actor names and summaries in the history screen;
  * the "📜 История" screen's admin gate, filter and pagination;
  * retention pruning.
"""
import datetime
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.db import (
    add_homework, cleanup_old_audit_logs, count_audit_logs, get_audit_logs,
    get_chat, get_homework_by_id, get_or_create_chat, migrate_chat,
    set_hw_edit_policy,
)
import handlers.history as history
import services.audit as audit
from handlers.homework import (
    EditHomeworkStates, format_homework_card, initiate_edit_field,
    process_hw_complete, process_hw_delete_confirm, process_hw_restore,
    process_edit_value, show_edit_menu,
)
from services.permissions import (
    POLICY_ADMIN_ONLY, POLICY_COLLABORATIVE, POLICY_CREATOR_OR_ADMIN,
    can_edit_homework, normalize_policy,
)

GROUP_ID = -1001234500
OTHER_GROUP_ID = -1001234501
PRIVATE_ID = 4242

ADMIN_ID = 111
AUTHOR_ID = 222
STRANGER_ID = 333


class FakeBot:
    def __init__(self, admins=None):
        self.admins = admins or set()

    async def get_chat_member(self, chat_id, user_id):
        status = "administrator" if user_id in self.admins else "member"
        return SimpleNamespace(status=status)


class FakeMessage:
    def __init__(self, chat_id, text=None, chat_type="group", user_id=AUTHOR_ID,
                 name="Аня", bot=None):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, full_name=name, first_name=name)
        self.bot = bot or FakeBot({ADMIN_ID})
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def delete(self):
        pass


class FakeCallback:
    def __init__(self, chat_id, data, chat_type="group", user_id=AUTHOR_ID,
                 name="Аня", bot=None):
        self.message = FakeMessage(chat_id, chat_type=chat_type, user_id=user_id,
                                   name=name, bot=bot)
        self.data = data
        self.from_user = self.message.from_user
        self.bot = self.message.bot
        self.alerts = []
        self.notices = []

    async def answer(self, *args, **kwargs):
        text = args[0] if args else kwargs.get("text")
        if kwargs.get("show_alert"):
            self.alerts.append(text)
        else:
            self.notices.append(text)

    @property
    def replies(self):
        """Everything the handler said back, alert or toast.

        ``require_admin`` picks ``show_alert`` via ``isinstance(event,
        CallbackQuery)``, which a lightweight fake isn't — so a rejection can
        arrive either way and tests must accept both.
        """
        return [text for text in self.alerts + self.notices if text]


def _state(chat_id=GROUP_ID):
    return FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


async def _setup(policy=POLICY_COLLABORATIVE, chat_id=GROUP_ID, chat_type="group"):
    await get_or_create_chat(chat_id, chat_type)
    if policy != POLICY_COLLABORATIVE:
        assert await set_hw_edit_policy(chat_id, policy)
    return await get_chat(chat_id)


async def _hw(chat_id=GROUP_ID, actor_user_id=AUTHOR_ID, actor_name="Аня"):
    return await add_homework(
        chat_id, "Математика", datetime.date(2026, 3, 10), "стр. 5",
        actor_user_id=actor_user_id, actor_name=actor_name,
    )


# --- Authorship stamping ----------------------------------------------------

async def test_create_stamps_author_and_timestamps(db):
    await _setup()
    hw = await _hw()
    assert hw.created_by_user_id == AUTHOR_ID
    assert hw.created_by_name == "Аня"
    # created_* and updated_* start out identical.
    assert hw.updated_by_user_id == AUTHOR_ID
    assert hw.created_at and hw.created_at == hw.updated_at


async def test_update_changes_updated_by_but_not_created_by(db):
    await _setup()
    hw = await _hw()
    from database.db import update_homework
    await update_homework(
        GROUP_ID, hw.id, subject_name="Алгебра",
        actor_user_id=ADMIN_ID, actor_name="Борис",
    )
    fresh = await get_homework_by_id(GROUP_ID, hw.id)
    assert fresh.created_by_user_id == AUTHOR_ID, "the original author must not be rewritten"
    assert fresh.created_by_name == "Аня"
    assert fresh.updated_by_user_id == ADMIN_ID
    assert fresh.updated_by_name == "Борис"
    assert fresh.updated_at >= fresh.created_at


async def test_legacy_row_keeps_null_authorship(db):
    """A record written without an actor (pre-authorship data) stays NULL."""
    await _setup()
    hw = await add_homework(GROUP_ID, "История", datetime.date(2026, 3, 11), "конспект")
    assert hw.created_by_user_id is None
    assert hw.created_by_name is None
    assert hw.updated_by_user_id is None


async def test_update_of_legacy_row_does_not_invent_a_creator(db):
    await _setup()
    hw = await add_homework(GROUP_ID, "История", datetime.date(2026, 3, 11), "конспект")
    from database.db import update_homework
    await update_homework(GROUP_ID, hw.id, description="новый текст",
                          actor_user_id=ADMIN_ID, actor_name="Борис")
    fresh = await get_homework_by_id(GROUP_ID, hw.id)
    assert fresh.created_by_user_id is None, "editing must not backfill an author"
    assert fresh.updated_by_user_id == ADMIN_ID


async def test_card_shows_author_and_marks_unknown_one(db):
    await _setup()
    authored = await _hw()
    legacy = await add_homework(GROUP_ID, "История", datetime.date(2026, 3, 11), "конспект")

    assert "Аня" in format_homework_card(authored)
    assert "Автор неизвестен" in format_homework_card(legacy)


# --- Policy: pure decision function ----------------------------------------

async def test_collaborative_allows_any_member(db):
    chat = await _setup(POLICY_COLLABORATIVE)
    hw = await _hw()
    bot = FakeBot({ADMIN_ID})
    for user_id in (ADMIN_ID, AUTHOR_ID, STRANGER_ID):
        assert await can_edit_homework(bot, chat, hw, user_id) is True


async def test_creator_or_admin_allows_author_and_admin_only(db):
    chat = await _setup(POLICY_CREATOR_OR_ADMIN)
    hw = await _hw()
    bot = FakeBot({ADMIN_ID})
    assert await can_edit_homework(bot, chat, hw, AUTHOR_ID) is True
    assert await can_edit_homework(bot, chat, hw, ADMIN_ID) is True
    assert await can_edit_homework(bot, chat, hw, STRANGER_ID) is False


async def test_creator_or_admin_allows_anyone_for_legacy_null_author(db):
    """
    A pre-authorship entry has no creator to compare against; locking the class
    out of its own existing homework would be worse than allowing the edit.
    A chat that wants those restricted picks admin_only.
    """
    chat = await _setup(POLICY_CREATOR_OR_ADMIN)
    hw = await add_homework(GROUP_ID, "История", datetime.date(2026, 3, 11), "конспект")
    bot = FakeBot({ADMIN_ID})
    assert await can_edit_homework(bot, chat, hw, STRANGER_ID) is True


async def test_admin_only_rejects_author_and_stranger(db):
    chat = await _setup(POLICY_ADMIN_ONLY)
    hw = await _hw()
    bot = FakeBot({ADMIN_ID})
    assert await can_edit_homework(bot, chat, hw, ADMIN_ID) is True
    assert await can_edit_homework(bot, chat, hw, AUTHOR_ID) is False
    assert await can_edit_homework(bot, chat, hw, STRANGER_ID) is False


async def test_admin_only_restricts_legacy_null_author_too(db):
    chat = await _setup(POLICY_ADMIN_ONLY)
    hw = await add_homework(GROUP_ID, "История", datetime.date(2026, 3, 11), "конспект")
    bot = FakeBot({ADMIN_ID})
    assert await can_edit_homework(bot, chat, hw, STRANGER_ID) is False


async def test_private_chat_is_never_restricted(db):
    chat = await _setup(POLICY_ADMIN_ONLY, chat_id=PRIVATE_ID, chat_type="private")
    hw = await _hw(PRIVATE_ID)
    assert await can_edit_homework(FakeBot(), chat, hw, STRANGER_ID) is True


async def test_unknown_policy_value_falls_back_to_collaborative(db):
    assert normalize_policy("nonsense") == POLICY_COLLABORATIVE
    assert normalize_policy(None) == POLICY_COLLABORATIVE
    # And a bad value can't be stored in the first place.
    await _setup()
    assert await set_hw_edit_policy(GROUP_ID, "nonsense") is False
    assert (await get_chat(GROUP_ID)).hw_edit_policy == POLICY_COLLABORATIVE


# --- Policy: enforced by the real handlers (not just hidden buttons) --------

async def test_stranger_cannot_open_edit_menu_under_creator_or_admin(db):
    await _setup(POLICY_CREATOR_OR_ADMIN)
    hw = await _hw()
    cb = FakeCallback(GROUP_ID, f"hw_edit_menu:{hw.id}:0:0", user_id=STRANGER_ID, name="Чужой")
    await show_edit_menu(cb, _state())
    assert cb.alerts, "a member who is neither author nor admin must be told why"
    assert "автор" in cb.alerts[0].lower()


async def test_stranger_cannot_write_new_value_even_with_forged_state(db):
    """
    The policy is re-checked at write time: a hand-crafted FSM state that skips
    the menu must still not be able to modify the entry.
    """
    await _setup(POLICY_CREATOR_OR_ADMIN)
    hw = await _hw()
    state = _state()
    await state.update_data(edit_hw_id=hw.id, edit_field="subject", edit_is_archive=0, edit_page=0)
    await state.set_state(EditHomeworkStates.waiting_for_new_value)

    msg = FakeMessage(GROUP_ID, text="Взломано", user_id=STRANGER_ID, name="Чужой")
    await process_edit_value(msg, state)

    assert (await get_homework_by_id(GROUP_ID, hw.id)).subject_name == "Математика"
    assert any("автор" in a[0].lower() for a in msg.answers)


async def test_author_can_edit_under_creator_or_admin(db):
    await _setup(POLICY_CREATOR_OR_ADMIN)
    hw = await _hw()
    cb = FakeCallback(GROUP_ID, f"hw_edit_field:{hw.id}:subject:0:0", user_id=AUTHOR_ID)
    state = _state()
    await initiate_edit_field(cb, state)
    assert not cb.alerts
    assert await state.get_state() == EditHomeworkStates.waiting_for_new_value.state

    msg = FakeMessage(GROUP_ID, text="Алгебра", user_id=AUTHOR_ID)
    await process_edit_value(msg, state)
    assert (await get_homework_by_id(GROUP_ID, hw.id)).subject_name == "Алгебра"


async def test_admin_can_edit_under_admin_only(db):
    await _setup(POLICY_ADMIN_ONLY)
    hw = await _hw()
    cb = FakeCallback(GROUP_ID, f"hw_edit_field:{hw.id}:desc:0:0", user_id=ADMIN_ID, name="Борис")
    state = _state()
    await initiate_edit_field(cb, state)
    assert not cb.alerts

    msg = FakeMessage(GROUP_ID, text="стр. 9", user_id=ADMIN_ID, name="Борис")
    await process_edit_value(msg, state)
    assert (await get_homework_by_id(GROUP_ID, hw.id)).description == "стр. 9"


async def test_stranger_cannot_complete_or_delete_under_admin_only(db):
    await _setup(POLICY_ADMIN_ONLY)
    hw = await _hw()

    complete_cb = FakeCallback(GROUP_ID, f"hw_complete:{hw.id}:0", user_id=STRANGER_ID)
    await process_hw_complete(complete_cb)
    assert complete_cb.alerts
    assert (await get_homework_by_id(GROUP_ID, hw.id)).is_completed is False

    delete_cb = FakeCallback(GROUP_ID, f"hw_delete_confirm:{hw.id}:0:0", user_id=STRANGER_ID)
    await process_hw_delete_confirm(delete_cb)
    assert delete_cb.alerts
    assert await get_homework_by_id(GROUP_ID, hw.id) is not None


async def test_any_member_can_still_complete_under_collaborative(db):
    await _setup(POLICY_COLLABORATIVE)
    hw = await _hw()
    cb = FakeCallback(GROUP_ID, f"hw_complete:{hw.id}:0", user_id=STRANGER_ID, name="Чужой")
    await process_hw_complete(cb)
    assert not cb.alerts
    assert (await get_homework_by_id(GROUP_ID, hw.id)).is_completed is True


# --- Audit entries ----------------------------------------------------------

async def test_audit_records_complete_and_restore(db):
    await _setup()
    hw = await _hw()

    await process_hw_complete(FakeCallback(GROUP_ID, f"hw_complete:{hw.id}:0"))
    await process_hw_restore(FakeCallback(GROUP_ID, f"hw_restore:{hw.id}:0"))

    entries = await get_audit_logs(GROUP_ID, limit=10)
    actions = [e.action for e in entries]
    assert actions[0] == audit.ACTION_RESTORE  # newest first
    assert audit.ACTION_COMPLETE in actions
    assert all(e.entity_type == audit.ENTITY_HOMEWORK for e in entries)
    assert all(e.actor_user_id == AUTHOR_ID for e in entries)


async def test_audit_survives_the_deleted_record(db):
    await _setup()
    hw = await _hw()
    await process_hw_delete_confirm(FakeCallback(GROUP_ID, f"hw_delete_confirm:{hw.id}:0:0"))

    assert await get_homework_by_id(GROUP_ID, hw.id) is None
    entries = await get_audit_logs(GROUP_ID, limit=10)
    deleted = [e for e in entries if e.action == audit.ACTION_DELETE]
    assert len(deleted) == 1
    assert deleted[0].entity_id == hw.id
    assert "Математика" in deleted[0].summary


async def test_audit_update_names_the_field_but_not_the_value(db):
    await _setup()
    hw = await _hw()
    cb = FakeCallback(GROUP_ID, f"hw_edit_field:{hw.id}:desc:0:0")
    state = _state()
    await initiate_edit_field(cb, state)
    await process_edit_value(FakeMessage(GROUP_ID, text="совершенно секретный текст"), state)

    entry = (await get_audit_logs(GROUP_ID, limit=1))[0]
    assert entry.action == audit.ACTION_UPDATE
    assert "описание" in entry.summary
    assert "секретный" not in entry.summary, "the journal must not store the new value"


async def test_audit_summary_is_truncated(db):
    await _setup()
    long = "x" * 500
    await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE,
                       summary=audit.summarize(long))
    entry = (await get_audit_logs(GROUP_ID, limit=1))[0]
    assert len(entry.summary) <= audit.AUDIT_SUMMARY_MAX


async def test_audit_rejects_unknown_entity_or_action(db):
    await _setup()
    await audit.record(GROUP_ID, "bogus", audit.ACTION_CREATE)
    await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, "bogus")
    assert await count_audit_logs(GROUP_ID) == 0


async def test_audit_is_isolated_per_chat(db):
    await _setup(chat_id=GROUP_ID)
    await _setup(chat_id=OTHER_GROUP_ID)
    hw_a = await _hw(GROUP_ID)
    hw_b = await _hw(OTHER_GROUP_ID)
    await process_hw_complete(FakeCallback(GROUP_ID, f"hw_complete:{hw_a.id}:0"))
    await process_hw_complete(FakeCallback(OTHER_GROUP_ID, f"hw_complete:{hw_b.id}:0"))

    a_entries = await get_audit_logs(GROUP_ID, limit=50)
    b_entries = await get_audit_logs(OTHER_GROUP_ID, limit=50)
    assert a_entries and b_entries
    assert all(e.chat_id == GROUP_ID for e in a_entries)
    assert all(e.chat_id == OTHER_GROUP_ID for e in b_entries)
    assert await count_audit_logs(GROUP_ID) == len(a_entries)


async def test_audit_filter_by_entity_type(db):
    await _setup()
    await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, summary="hw")
    await audit.record(GROUP_ID, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE, summary="cfg")
    assert await count_audit_logs(GROUP_ID) == 2
    assert await count_audit_logs(GROUP_ID, audit.ENTITY_SETTINGS) == 1
    only = await get_audit_logs(GROUP_ID, audit.ENTITY_SETTINGS, limit=10)
    assert [e.summary for e in only] == ["cfg"]


async def test_chat_reset_removes_its_history(db):
    """A full reset deletes the chat; its journal must cascade away with it."""
    from database.db import delete_chat
    await _setup()
    await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, summary="hw")
    await delete_chat(GROUP_ID)
    assert await count_audit_logs(GROUP_ID) == 0


async def test_migrate_chat_moves_policy_and_history(db):
    await _setup(POLICY_ADMIN_ONLY, chat_id=GROUP_ID)
    await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, summary="hw")

    assert await migrate_chat(GROUP_ID, OTHER_GROUP_ID) is True
    assert (await get_chat(OTHER_GROUP_ID)).hw_edit_policy == POLICY_ADMIN_ONLY
    assert await count_audit_logs(OTHER_GROUP_ID) == 1
    assert await count_audit_logs(GROUP_ID) == 0


# --- Retention --------------------------------------------------------------

async def test_retention_prunes_only_old_entries(db):
    await _setup()
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)).isoformat()
    from database.db import add_audit_log
    await add_audit_log(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, created_at=old)
    await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, summary="fresh")

    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=180)).isoformat()
    assert await cleanup_old_audit_logs(cutoff) == 1
    remaining = await get_audit_logs(GROUP_ID, limit=10)
    assert [e.summary for e in remaining] == ["fresh"]


async def test_scheduler_prune_respects_disabled_retention(db, monkeypatch):
    import services.scheduler as scheduler
    await _setup()
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)).isoformat()
    from database.db import add_audit_log
    await add_audit_log(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, created_at=old)

    monkeypatch.setattr(scheduler, "AUDIT_RETENTION_DAYS", 0)
    assert await scheduler.prune_audit_log() == 0
    assert await count_audit_logs(GROUP_ID) == 1

    monkeypatch.setattr(scheduler, "AUDIT_RETENTION_DAYS", 180)
    assert await scheduler.prune_audit_log() == 1
    assert await count_audit_logs(GROUP_ID) == 0


# --- History screen ---------------------------------------------------------

async def test_history_is_admin_only_in_a_group(db):
    await _setup()
    cb = FakeCallback(GROUP_ID, "au_open", user_id=STRANGER_ID)
    await history.open_history(cb, _state())
    assert cb.replies, "a non-admin must get a clear message, not a silent no-op"
    assert "администратор" in cb.replies[0]
    assert not cb.message.answers, "and must not see any history content"


async def test_history_open_for_admin_and_in_private(db):
    await _setup()
    await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, summary="Математика")
    cb = FakeCallback(GROUP_ID, "au_open", user_id=ADMIN_ID, name="Борис")
    await history.open_history(cb, _state())
    assert cb.message.answers
    assert "История изменений" in cb.message.answers[0][0]

    await _setup(chat_id=PRIVATE_ID, chat_type="private")
    priv = FakeCallback(PRIVATE_ID, "au_open", chat_type="private", user_id=STRANGER_ID)
    await history.open_history(priv, _state(PRIVATE_ID))
    assert priv.message.answers


async def test_history_empty_state(db):
    await _setup()
    text, _ = await history.render_history(GROUP_ID, "all", 0)
    assert "Пока ничего не записано" in text


async def test_history_pagination_and_filter(db):
    await _setup()
    for i in range(history.PAGE_SIZE + 3):
        await audit.record(GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE, summary=f"hw{i}")
    await audit.record(GROUP_ID, audit.ENTITY_SETTINGS, audit.ACTION_UPDATE, summary="cfg")

    page0, _ = await history.render_history(GROUP_ID, "all", 0)
    assert "стр. 1/2" in page0
    page1, _ = await history.render_history(GROUP_ID, "all", 1)
    assert "стр. 2/2" in page1
    # An out-of-range page clamps to the last one instead of erroring.
    clamped, _ = await history.render_history(GROUP_ID, "all", 99)
    assert "стр. 2/2" in clamped

    filtered, _ = await history.render_history(GROUP_ID, "settings", 0)
    assert "cfg" in filtered and "hw0" not in filtered


async def test_history_rejects_malformed_or_unknown_filter(db):
    await _setup()
    for data in ("au_page:all", "au_page:bogus:0", "au_page:all:x"):
        cb = FakeCallback(GROUP_ID, data, user_id=ADMIN_ID)
        await history.page_history(cb, _state())
        assert cb.alerts, f"{data} must be rejected as stale"


async def test_history_escapes_actor_name_and_summary(db):
    """A user calling themselves <b>evil</b> must not be able to inject HTML."""
    await _setup()
    await audit.record(
        GROUP_ID, audit.ENTITY_HOMEWORK, audit.ACTION_CREATE,
        actor_user_id=STRANGER_ID, actor_name="<b>Зло</b>",
        summary=audit.summarize("<i>&подстава</i>"),
    )
    text, _ = await history.render_history(GROUP_ID, "all", 0)
    assert "&lt;b&gt;Зло&lt;/b&gt;" in text
    assert "&lt;i&gt;&amp;подстава&lt;/i&gt;" in text
    assert "<b>Зло</b>" not in text


async def test_actor_name_is_trimmed_and_username_not_stored(db):
    long_name = "Я" * (audit.ACTOR_NAME_MAX + 50)
    event = SimpleNamespace(from_user=SimpleNamespace(
        id=STRANGER_ID, full_name=long_name, first_name="Я", username="secret_handle",
    ))
    user_id, name = audit.actor_from(event)
    assert user_id == STRANGER_ID
    assert len(name) == audit.ACTOR_NAME_MAX
    assert "secret_handle" not in (name or "")


async def test_actor_from_without_user_is_unknown(db):
    user_id, name = audit.actor_from(SimpleNamespace())
    assert user_id is None and name is None
    assert audit.actor_label(None, None) == audit.UNKNOWN_ACTOR
