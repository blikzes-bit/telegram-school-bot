"""
Stage: photos and files attached to homework.

Covers: extraction/validation of incoming messages, the untrusted file name,
the FSM (both adding files to a brand-new entry and to an existing one), the
per-entry limit, duplicates, deletion with confirmation, the FK cascade,
chat_id isolation, the edit policy applying to attachments, entries that have
no attachments at all, and a Telegram API error on a dead ``file_id``.

No test writes or reads a binary: the bot only ever stores Telegram references.
"""
import datetime
from types import SimpleNamespace

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from database.db import (
    add_homework, add_homework_attachment, count_homework_attachments,
    delete_homework, delete_homework_attachment, get_attachment_counts,
    get_homework_attachment, get_homework_attachments, get_or_create_chat,
    set_hw_edit_policy,
)
import services.attachments as att
from handlers.homework import (
    AddHomeworkStates, EditHomeworkStates, format_homework_card,
    format_homework_list, process_add_attachment, process_attachment_add,
    process_attachment_delete, process_attachment_delete_ask,
    process_attachment_menu, process_attachments_done, process_attachment_upload,
    process_description, process_due_date_callback, process_hw_view_actions,
    render_attachment_menu, send_attachments,
)
from services.permissions import POLICY_ADMIN_ONLY
from utils import (
    MAX_ATTACHMENTS_PER_HOMEWORK, MAX_ATTACHMENT_CAPTION_LEN,
    MAX_ATTACHMENT_SIZE_BYTES, format_file_size, safe_file_name,
)

CHAT_ID = 707070
OTHER_CHAT_ID = 707071
GROUP_ID = -1007070700
ADMIN_ID = 11
AUTHOR_ID = 22
STRANGER_ID = 33


class FakeBot:
    def __init__(self, admins=None):
        self.admins = admins or set()

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(
            status="administrator" if user_id in self.admins else "member"
        )


def photo_sizes(*, unique="pu1", size=120_000):
    """Telegram sends several PhotoSize entries; the largest must be picked."""
    return [
        SimpleNamespace(file_id=f"{unique}-small", file_unique_id=f"{unique}-small",
                        file_size=size // 10, width=90),
        SimpleNamespace(file_id=f"{unique}-big", file_unique_id=unique,
                        file_size=size, width=1280),
    ]


def document(*, unique="du1", name="lesson.pdf", size=40_000):
    return SimpleNamespace(
        file_id=f"{unique}-fid", file_unique_id=unique, file_name=name, file_size=size
    )


class FakeMessage:
    def __init__(self, chat_id=CHAT_ID, text=None, chat_type="private",
                 user_id=AUTHOR_ID, name="Аня", photo=None, doc=None,
                 caption=None, video=None, bot=None, fail_send=False):
        self.text = text
        self.caption = caption
        self.photo = photo
        self.document = doc
        self.video = video
        self.chat = SimpleNamespace(id=chat_id, type=chat_type)
        self.from_user = SimpleNamespace(id=user_id, full_name=name, first_name=name)
        self.bot = bot or FakeBot({ADMIN_ID})
        self.fail_send = fail_send
        self.answers = []
        self.photos_sent = []
        self.docs_sent = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append((text, kwargs))

    async def answer_photo(self, file_id, **kwargs):
        if self.fail_send:
            raise RuntimeError("Bad Request: wrong file identifier")
        self.photos_sent.append((file_id, kwargs))

    async def answer_document(self, file_id, **kwargs):
        if self.fail_send:
            raise RuntimeError("Bad Request: wrong file identifier")
        self.docs_sent.append((file_id, kwargs))

    async def delete(self):
        pass

    @property
    def texts(self):
        return [a[0] for a in self.answers]


class FakeCallback:
    def __init__(self, data, chat_id=CHAT_ID, chat_type="private",
                 user_id=AUTHOR_ID, name="Аня", bot=None):
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
        return [t for t in self.alerts + self.notices if t]


def _state(chat_id=CHAT_ID):
    return FSMContext(storage=MemoryStorage(),
                      key=StorageKey(bot_id=1, chat_id=chat_id, user_id=chat_id))


async def _hw(chat_id=CHAT_ID, chat_type="private"):
    await get_or_create_chat(chat_id, chat_type)
    return await add_homework(
        chat_id, "Математика", datetime.date(2026, 5, 5), "стр. 7",
        actor_user_id=AUTHOR_ID, actor_name="Аня",
    )


async def _attach(hw, chat_id=CHAT_ID, unique="u1", kind="document", name="a.pdf"):
    return await add_homework_attachment(
        chat_id, hw.id, file_id=f"{unique}-fid", file_unique_id=unique,
        file_type=kind, file_name=name, file_size=1234,
        actor_user_id=AUTHOR_ID, actor_name="Аня",
    )


# --- Extraction / validation ------------------------------------------------

def test_extract_photo_picks_the_largest_size():
    info, error = att.extract_attachment(FakeMessage(photo=photo_sizes()))
    assert error is None
    assert info.file_type == "photo"
    assert info.file_unique_id == "pu1"
    assert info.file_id.endswith("-big")
    assert info.file_name is None


def test_extract_document_keeps_name_and_size():
    info, error = att.extract_attachment(FakeMessage(doc=document()))
    assert error is None
    assert info.file_type == "document"
    assert info.file_name == "lesson.pdf"
    assert info.file_size == 40_000


def test_image_sent_as_file_stays_a_document():
    """Sending a picture "as file" keeps its quality — we must not re-label it."""
    info, _ = att.extract_attachment(FakeMessage(doc=document(name="scan.png")))
    assert info.file_type == "document"
    assert info.file_name == "scan.png"


def test_extract_rejects_unsupported_kinds():
    info, error = att.extract_attachment(
        FakeMessage(video=SimpleNamespace(file_id="v", file_unique_id="vu"))
    )
    assert info is None
    assert "фотографию или файл" in error


def test_extract_rejects_plain_text_with_a_hint():
    info, error = att.extract_attachment(FakeMessage(text="просто текст"))
    assert info is None
    assert "Готово" in error


def test_extract_rejects_oversized_file():
    big = document(size=MAX_ATTACHMENT_SIZE_BYTES + 1)
    info, error = att.extract_attachment(FakeMessage(doc=big))
    assert info is None
    assert "слишком большой" in error


def test_extract_accepts_file_at_the_limit():
    info, error = att.extract_attachment(
        FakeMessage(doc=document(size=MAX_ATTACHMENT_SIZE_BYTES))
    )
    assert error is None and info is not None


def test_long_caption_is_truncated_not_rejected():
    info, error = att.extract_attachment(
        FakeMessage(doc=document(), caption="я" * (MAX_ATTACHMENT_CAPTION_LEN + 200))
    )
    assert error is None
    assert len(info.caption) <= MAX_ATTACHMENT_CAPTION_LEN
    assert info.caption.endswith("…")


def test_blank_caption_becomes_none():
    info, _ = att.extract_attachment(FakeMessage(doc=document(), caption="   \n  "))
    assert info.caption is None


# --- The file name is untrusted --------------------------------------------

def test_safe_file_name_strips_directories():
    assert safe_file_name("../../etc/passwd") == "passwd"
    assert safe_file_name(r"C:\Windows\System32\evil.dll") == "evil.dll"


def test_safe_file_name_strips_bidi_and_control_chars():
    """``report\\u202Egnp.exe`` renders as ``reportexe.png`` — a classic disguise."""
    cleaned = safe_file_name("report\u202egnp.exe")
    assert "\u202e" not in cleaned
    assert cleaned == "reportgnp.exe"
    assert safe_file_name("a\x00b\x1fc.txt") == "abc.txt"


def test_safe_file_name_handles_empty_and_dot_names():
    for value in (None, "", "   ", ".", "..", "/", "../"):
        assert safe_file_name(value) is None


def test_safe_file_name_is_length_capped():
    from utils import MAX_FILE_NAME_LEN
    assert len(safe_file_name("x" * 500)) == MAX_FILE_NAME_LEN


def test_document_name_is_sanitised_on_the_way_in():
    info, _ = att.extract_attachment(
        FakeMessage(doc=document(name="../../../secret/passwd"))
    )
    assert info.file_name == "passwd"


def test_format_file_size():
    assert format_file_size(None) == ""
    assert format_file_size(0) == ""
    assert format_file_size(512) == "512 Б"
    assert format_file_size(2048) == "2 КБ"
    assert "МБ" in format_file_size(5 * 1024 * 1024)


# --- Storage ----------------------------------------------------------------

async def test_add_and_read_attachments(db):
    hw = await _hw()
    result = await _attach(hw, unique="one")
    assert result.status == "ok"
    assert result.attachment.created_by_user_id == AUTHOR_ID
    assert result.attachment.created_at

    stored = await get_homework_attachments(CHAT_ID, hw.id)
    assert [a.file_unique_id for a in stored] == ["one"]
    assert await count_homework_attachments(CHAT_ID, hw.id) == 1


async def test_attachment_limit_is_enforced(db):
    hw = await _hw()
    for i in range(MAX_ATTACHMENTS_PER_HOMEWORK):
        assert (await _attach(hw, unique=f"u{i}")).status == "ok"
    over = await _attach(hw, unique="one-too-many")
    assert over.status == "limit"
    assert await count_homework_attachments(CHAT_ID, hw.id) == MAX_ATTACHMENTS_PER_HOMEWORK


async def test_duplicate_file_is_reported_not_stored_twice(db):
    hw = await _hw()
    assert (await _attach(hw, unique="same")).status == "ok"
    assert (await _attach(hw, unique="same")).status == "duplicate"
    assert await count_homework_attachments(CHAT_ID, hw.id) == 1


async def test_same_file_may_be_attached_to_two_different_homeworks(db):
    first = await _hw()
    second = await add_homework(CHAT_ID, "Физика", datetime.date(2026, 5, 6), "лаба")
    assert (await _attach(first, unique="shared")).status == "ok"
    assert (await _attach(second, unique="shared")).status == "ok"


async def test_attaching_to_a_foreign_chats_homework_is_refused(db):
    hw = await _hw()
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    result = await add_homework_attachment(
        OTHER_CHAT_ID, hw.id, file_id="f", file_unique_id="x", file_type="photo"
    )
    assert result.status == "missing"
    assert await count_homework_attachments(CHAT_ID, hw.id) == 0


async def test_reads_are_scoped_to_chat_id(db):
    hw = await _hw()
    await _attach(hw, unique="mine")
    await get_or_create_chat(OTHER_CHAT_ID, "private")
    assert await get_homework_attachments(OTHER_CHAT_ID, hw.id) == []
    stored = (await get_homework_attachments(CHAT_ID, hw.id))[0]
    assert await get_homework_attachment(OTHER_CHAT_ID, stored.id) is None
    assert await delete_homework_attachment(OTHER_CHAT_ID, stored.id) is False
    assert await count_homework_attachments(CHAT_ID, hw.id) == 1


async def test_deleting_homework_cascades_to_its_attachments(db):
    hw = await _hw()
    await _attach(hw, unique="doomed")
    assert await delete_homework(CHAT_ID, hw.id) is True
    # No orphan rows may survive the parent.
    assert await get_homework_attachments(CHAT_ID, hw.id) == []
    assert await get_attachment_counts(CHAT_ID) == {}


async def test_attachment_counts_batches_the_whole_list(db):
    first = await _hw()
    second = await add_homework(CHAT_ID, "Физика", datetime.date(2026, 5, 6), "лаба")
    await _attach(first, unique="a")
    await _attach(first, unique="b")
    counts = await get_attachment_counts(CHAT_ID)
    assert counts == {first.id: 2}
    assert second.id not in counts


async def test_delete_one_attachment(db):
    hw = await _hw()
    await _attach(hw, unique="a")
    await _attach(hw, unique="b")
    stored = await get_homework_attachments(CHAT_ID, hw.id)
    assert await delete_homework_attachment(CHAT_ID, stored[0].id) is True
    assert [a.file_unique_id for a in await get_homework_attachments(CHAT_ID, hw.id)] == ["b"]
    # Deleting it again is a harmless no-op.
    assert await delete_homework_attachment(CHAT_ID, stored[0].id) is False


# --- Add-homework FSM -------------------------------------------------------

async def _start_attachment_step(chat_id=CHAT_ID):
    """Drive subject+description so the flow sits on the attachment step."""
    await get_or_create_chat(chat_id, "private")
    state = _state(chat_id)
    await state.update_data(hw_subject="Математика")
    await state.set_state(AddHomeworkStates.waiting_for_description)
    msg = FakeMessage(chat_id, text="стр. 7")
    await process_description(msg, state)
    return state, msg


async def test_flow_asks_for_attachments_after_the_description(db):
    state, msg = await _start_attachment_step()
    assert await state.get_state() == AddHomeworkStates.waiting_for_attachments.state
    assert "Вложения" in msg.texts[-1]
    assert (await state.get_data())["hw_files"] == []


async def test_flow_collects_several_attachments(db):
    state, _ = await _start_attachment_step()
    await process_add_attachment(FakeMessage(photo=photo_sizes(unique="p1")), state)
    await process_add_attachment(FakeMessage(doc=document(unique="d1")), state)
    files = (await state.get_data())["hw_files"]
    assert [f["file_unique_id"] for f in files] == ["p1", "d1"]
    assert [f["file_type"] for f in files] == ["photo", "document"]


async def test_flow_rejects_duplicate_and_unsupported_without_losing_state(db):
    state, _ = await _start_attachment_step()
    await process_add_attachment(FakeMessage(photo=photo_sizes(unique="p1")), state)

    dup = FakeMessage(photo=photo_sizes(unique="p1"))
    await process_add_attachment(dup, state)
    assert "уже приложен" in dup.texts[-1]

    bad = FakeMessage(video=SimpleNamespace(file_id="v", file_unique_id="vu"))
    await process_add_attachment(bad, state)
    assert "фотографию или файл" in bad.texts[-1]

    assert await state.get_state() == AddHomeworkStates.waiting_for_attachments.state
    assert len((await state.get_data())["hw_files"]) == 1


async def test_flow_stops_at_the_limit(db):
    state, _ = await _start_attachment_step()
    for i in range(MAX_ATTACHMENTS_PER_HOMEWORK):
        await process_add_attachment(FakeMessage(doc=document(unique=f"d{i}")), state)
    extra = FakeMessage(doc=document(unique="too-many"))
    await process_add_attachment(extra, state)
    assert "Больше" in extra.texts[-1]
    assert len((await state.get_data())["hw_files"]) == MAX_ATTACHMENTS_PER_HOMEWORK


async def test_flow_cancel_from_the_attachment_step(db):
    state, _ = await _start_attachment_step()
    msg = FakeMessage(text="❌ Отмена")
    await process_add_attachment(msg, state)
    assert await state.get_state() is None
    assert "отменено" in msg.texts[-1]


async def test_done_moves_to_the_due_date_step(db):
    state, _ = await _start_attachment_step()
    await process_add_attachment(FakeMessage(doc=document(unique="d1")), state)
    cb = FakeCallback("hwa_files_done")
    await process_attachments_done(cb, state)
    assert await state.get_state() == AddHomeworkStates.waiting_for_due_date.state
    assert "Вложений: <b>1</b>" in cb.message.texts[-1]


async def test_buffered_files_are_persisted_with_the_new_entry(db):
    state, _ = await _start_attachment_step()
    await process_add_attachment(FakeMessage(photo=photo_sizes(unique="p1")), state)
    await process_add_attachment(FakeMessage(doc=document(unique="d1")), state)
    await process_attachments_done(FakeCallback("hwa_files_done"), state)

    due = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    await process_due_date_callback(FakeCallback(f"hwa_date:{due}"), state)

    from database.db import get_homework
    hw = (await get_homework(CHAT_ID))[0]
    stored = await get_homework_attachments(CHAT_ID, hw.id)
    assert [a.file_unique_id for a in stored] == ["p1", "d1"]
    assert stored[0].created_by_user_id == AUTHOR_ID


async def test_skipping_attachments_creates_a_plain_entry(db):
    state, _ = await _start_attachment_step()
    await process_attachments_done(FakeCallback("hwa_files_done"), state)
    due = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
    await process_due_date_callback(FakeCallback(f"hwa_date:{due}"), state)

    from database.db import get_homework
    hw = (await get_homework(CHAT_ID))[0]
    assert await get_homework_attachments(CHAT_ID, hw.id) == []


# --- Attaching to an existing entry -----------------------------------------

async def test_add_attachment_to_existing_homework(db):
    hw = await _hw()
    state = _state()
    open_cb = FakeCallback(f"hw_att_add:{hw.id}:0:0")
    await process_attachment_add(open_cb, state)
    assert await state.get_state() == EditHomeworkStates.waiting_for_attachment.state

    msg = FakeMessage(photo=photo_sizes(unique="added"))
    await process_attachment_upload(msg, state)

    stored = await get_homework_attachments(CHAT_ID, hw.id)
    assert [a.file_unique_id for a in stored] == ["added"]
    assert await state.get_state() is None
    assert any("добавлено" in t for t in msg.texts)


async def test_add_attachment_button_is_refused_at_the_limit(db):
    hw = await _hw()
    for i in range(MAX_ATTACHMENTS_PER_HOMEWORK):
        await _attach(hw, unique=f"u{i}")
    cb = FakeCallback(f"hw_att_add:{hw.id}:0:0")
    state = _state()
    await process_attachment_add(cb, state)
    assert cb.alerts and "нельзя" in cb.alerts[0]
    assert await state.get_state() is None


async def test_upload_of_unsupported_type_keeps_the_step_open(db):
    hw = await _hw()
    state = _state()
    await process_attachment_add(FakeCallback(f"hw_att_add:{hw.id}:0:0"), state)
    msg = FakeMessage(video=SimpleNamespace(file_id="v", file_unique_id="vu"))
    await process_attachment_upload(msg, state)
    assert await state.get_state() == EditHomeworkStates.waiting_for_attachment.state
    assert await get_homework_attachments(CHAT_ID, hw.id) == []


async def test_upload_for_meanwhile_deleted_homework(db):
    hw = await _hw()
    state = _state()
    await process_attachment_add(FakeCallback(f"hw_att_add:{hw.id}:0:0"), state)
    await delete_homework(CHAT_ID, hw.id)

    msg = FakeMessage(doc=document(unique="late"))
    await process_attachment_upload(msg, state)
    assert await state.get_state() is None
    assert any("не существует" in t for t in msg.texts)


async def test_duplicate_upload_to_existing_homework_keeps_step_open(db):
    hw = await _hw()
    await _attach(hw, unique="same")
    state = _state()
    await process_attachment_add(FakeCallback(f"hw_att_add:{hw.id}:0:0"), state)
    msg = FakeMessage(doc=document(unique="same"))
    await process_attachment_upload(msg, state)
    assert any("уже приложен" in t for t in msg.texts)
    assert await count_homework_attachments(CHAT_ID, hw.id) == 1


# --- Delete with confirmation ----------------------------------------------

async def test_delete_attachment_asks_before_removing(db):
    hw = await _hw()
    await _attach(hw, unique="a", name="task.pdf")
    stored = (await get_homework_attachments(CHAT_ID, hw.id))[0]

    ask = FakeCallback(f"hw_att_del_ask:{stored.id}:{hw.id}:0:0")
    await process_attachment_delete_ask(ask, _state())
    assert "Удалить вложение" in ask.message.texts[-1]
    # Nothing removed by merely asking.
    assert await count_homework_attachments(CHAT_ID, hw.id) == 1

    confirm = FakeCallback(f"hw_att_del:{stored.id}:{hw.id}:0:0")
    await process_attachment_delete(confirm, _state())
    assert await count_homework_attachments(CHAT_ID, hw.id) == 0


async def test_delete_ask_for_already_deleted_attachment(db):
    hw = await _hw()
    cb = FakeCallback(f"hw_att_del_ask:999999:{hw.id}:0:0")
    await process_attachment_delete_ask(cb, _state())
    assert cb.alerts and "уже удалено" in cb.alerts[0]


async def test_attachment_callbacks_reject_malformed_data(db):
    hw = await _hw()
    for data, handler in (
        (f"hw_att_menu:{hw.id}", process_attachment_menu),
        ("hw_att_add:x:0:0", process_attachment_add),
        ("hw_att_del_ask:1:2:3", process_attachment_delete_ask),
        ("hw_att_del:1:2:3", process_attachment_delete),
    ):
        cb = FakeCallback(data)
        await handler(cb, _state())
        assert cb.alerts, f"{data} must be rejected as stale"


# --- Policy applies to attachments -----------------------------------------

async def test_stranger_cannot_add_or_delete_attachments_under_admin_only(db):
    await get_or_create_chat(GROUP_ID, "group")
    assert await set_hw_edit_policy(GROUP_ID, POLICY_ADMIN_ONLY)
    hw = await add_homework(GROUP_ID, "Математика", datetime.date(2026, 5, 5), "стр. 7",
                            actor_user_id=AUTHOR_ID, actor_name="Аня")
    await add_homework_attachment(GROUP_ID, hw.id, file_id="f", file_unique_id="u",
                                  file_type="document", file_name="a.pdf")
    stored = (await get_homework_attachments(GROUP_ID, hw.id))[0]

    add_cb = FakeCallback(f"hw_att_add:{hw.id}:0:0", chat_id=GROUP_ID,
                          chat_type="group", user_id=STRANGER_ID, name="Чужой")
    await process_attachment_add(add_cb, _state(GROUP_ID))
    assert add_cb.replies

    del_cb = FakeCallback(f"hw_att_del:{stored.id}:{hw.id}:0:0", chat_id=GROUP_ID,
                          chat_type="group", user_id=STRANGER_ID, name="Чужой")
    await process_attachment_delete(del_cb, _state(GROUP_ID))
    assert await count_homework_attachments(GROUP_ID, hw.id) == 1, "must not be deleted"


async def test_admin_can_manage_attachments_under_admin_only(db):
    await get_or_create_chat(GROUP_ID, "group")
    assert await set_hw_edit_policy(GROUP_ID, POLICY_ADMIN_ONLY)
    hw = await add_homework(GROUP_ID, "Математика", datetime.date(2026, 5, 5), "стр. 7")

    state = _state(GROUP_ID)
    cb = FakeCallback(f"hw_att_add:{hw.id}:0:0", chat_id=GROUP_ID, chat_type="group",
                      user_id=ADMIN_ID, name="Борис")
    await process_attachment_add(cb, state)
    assert not cb.replies

    msg = FakeMessage(GROUP_ID, chat_type="group", user_id=ADMIN_ID, name="Борис",
                      doc=document(unique="ok"))
    await process_attachment_upload(msg, state)
    assert await count_homework_attachments(GROUP_ID, hw.id) == 1


# --- Rendering / sending ----------------------------------------------------

async def test_card_and_list_show_the_attachment_marker(db):
    hw = await _hw()
    await _attach(hw, unique="a")
    await _attach(hw, unique="b")
    assert "Вложений: <b>2</b>" in format_homework_card(hw, 2)
    text, _ = await format_homework_list(CHAT_ID)
    assert "📎2" in text


async def test_entry_without_attachments_renders_cleanly(db):
    """Pre-attachment homework has none; nothing may hint otherwise."""
    await get_or_create_chat(CHAT_ID, "private")
    hw = await add_homework(CHAT_ID, "История", datetime.date(2026, 5, 9), "конспект")
    assert "Вложений" not in format_homework_card(hw, 0)
    text, _ = await format_homework_list(CHAT_ID)
    assert "📎" not in text

    rendered = await render_attachment_menu(CHAT_ID, hw.id, False, 0)
    assert rendered is not None
    assert "Пока нет ни одного вложения" in rendered[0]


async def test_opening_a_card_sends_its_attachments(db):
    hw = await _hw()
    await _attach(hw, unique="p", kind="photo", name=None)
    await _attach(hw, unique="d", kind="document", name="task.pdf")

    cb = FakeCallback(f"hw_view_actions:{hw.id}:0:0")
    await process_hw_view_actions(cb, _state())
    assert len(cb.message.photos_sent) == 1
    assert len(cb.message.docs_sent) == 1
    assert "task.pdf" in cb.message.docs_sent[0][1]["caption"]


async def test_dead_file_id_explains_how_to_replace_it(db):
    """Telegram sometimes invalidates a file_id; say so instead of failing."""
    hw = await _hw()
    await _attach(hw, unique="stale", kind="document", name="old.pdf")
    attachments = await get_homework_attachments(CHAT_ID, hw.id)

    msg = FakeMessage(fail_send=True)
    delivered = await send_attachments(msg, attachments)
    assert delivered == 0
    assert any("приложи файл заново" in t for t in msg.texts)
    assert any("old.pdf" in t for t in msg.texts)


async def test_one_dead_attachment_does_not_block_the_others(db):
    hw = await _hw()
    await _attach(hw, unique="a", kind="document", name="a.pdf")
    await _attach(hw, unique="b", kind="document", name="b.pdf")
    attachments = await get_homework_attachments(CHAT_ID, hw.id)

    class PartlyFailing(FakeMessage):
        async def answer_document(self, file_id, **kwargs):
            if file_id == "a-fid":
                raise RuntimeError("wrong file identifier")
            self.docs_sent.append((file_id, kwargs))

    msg = PartlyFailing()
    assert await send_attachments(msg, attachments) == 1
    assert msg.docs_sent[0][0] == "b-fid"
    assert any("a.pdf" in t for t in msg.texts)


async def test_attachment_caption_and_name_are_html_escaped(db):
    hw = await _hw()
    await add_homework_attachment(
        CHAT_ID, hw.id, file_id="f", file_unique_id="u", file_type="document",
        file_name="<b>evil</b>.pdf", caption="<i>&подпись</i>",
    )
    rendered = await render_attachment_menu(CHAT_ID, hw.id, False, 0)
    text = rendered[0]
    assert "&lt;b&gt;evil&lt;/b&gt;.pdf" in text
    assert "&lt;i&gt;&amp;подпись&lt;/i&gt;" in text
    assert "<b>evil</b>.pdf" not in text

    attachments = await get_homework_attachments(CHAT_ID, hw.id)
    msg = FakeMessage()
    await send_attachments(msg, attachments)
    caption = msg.docs_sent[0][1]["caption"]
    assert "&lt;b&gt;evil&lt;/b&gt;.pdf" in caption
