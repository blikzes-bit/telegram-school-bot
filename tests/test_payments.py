"""
Lesson payments (tutor profile): storage, money formatting, rights and the
reminder.

The three things worth pinning down: money never becomes a float, the reminder
talks about exactly the entries the app marks as "due soon", and a chat that has
no payments never gets a payment message even though the switch defaults to on.
"""
import datetime

import pytest

import database.db as dbm
from services import permissions as perms, profiles
from tests.conftest import FakeBot
from tests.web_helpers import authenticate, build_test_settings, now_iso, web_client

TODAY = datetime.date(2024, 3, 10)


async def _tutor_chat(chat_id: int, owner_id: int):
    await dbm.get_or_create_chat(chat_id, "group")
    await dbm.finalize_onboarding(
        chat_id, "group", [], {}, profile=profiles.PROFILE_TUTOR,
    )
    await dbm.set_chat_owner(chat_id, owner_id)


async def _member(chat_id: int, user_id: int, role: str = "member", app_role=None):
    await dbm.upsert_membership(chat_id, user_id, role, now_iso())
    if app_role is not None:
        await dbm.set_member_app_role(chat_id, user_id, app_role)


# --- Money formatting ---------------------------------------------------------

@pytest.mark.parametrize(
    "minor,expected",
    [
        (0, "0 UAH"),
        (35000, "350 UAH"),
        (125050, "1 250,50 UAH"),
        (99, "0,99 UAH"),
        (100000000, "1 000 000 UAH"),
    ],
)
def test_amounts_are_formatted_once_server_side(minor, expected):
    assert profiles.format_amount(minor, "UAH") == expected


def test_payment_status_uses_its_own_reminder_window():
    """What the UI highlights and what the reminder sends must be one rule."""
    assert profiles.payment_status(TODAY, TODAY, True, 1) == "paid"
    assert profiles.payment_status(
        TODAY - datetime.timedelta(days=1), TODAY, False, 1
    ) == "overdue"
    assert profiles.payment_status(TODAY, TODAY, False, 0) == "due_soon"
    assert profiles.payment_status(
        TODAY + datetime.timedelta(days=3), TODAY, False, 3
    ) == "due_soon"
    assert profiles.payment_status(
        TODAY + datetime.timedelta(days=4), TODAY, False, 3
    ) == "upcoming"


# --- Storage ------------------------------------------------------------------

async def test_amounts_are_stored_as_integers(db):
    chat_id = -9001
    await _tutor_chat(chat_id, 1)
    payment = await dbm.add_payment(chat_id, "Март", 35000, TODAY, currency="UAH")
    assert isinstance(payment.amount_minor, int)
    assert payment.amount_minor == 35000


async def test_negative_amount_is_clamped_not_stored(db):
    chat_id = -9002
    await _tutor_chat(chat_id, 1)
    payment = await dbm.add_payment(chat_id, "Ошибка", -500, TODAY)
    assert payment.amount_minor == 0


async def test_unknown_period_falls_back_instead_of_breaking(db):
    chat_id = -9003
    await _tutor_chat(chat_id, 1)
    payment = await dbm.add_payment(chat_id, "X", 100, TODAY, period="whenever")
    assert payment.period == "one_time"
    assert await dbm.update_payment(chat_id, payment.id, period="whenever") is False


async def test_payments_are_scoped_to_their_chat(db):
    mine, theirs = -9004, -9005
    await _tutor_chat(mine, 1)
    await _tutor_chat(theirs, 2)
    foreign = await dbm.add_payment(theirs, "Чужое", 100, TODAY)

    assert await dbm.get_payment_by_id(mine, foreign.id) is None
    assert await dbm.delete_payment(mine, foreign.id) is False
    assert await dbm.get_payment_by_id(theirs, foreign.id) is not None


async def test_marking_paid_records_when_and_clears_on_undo(db):
    chat_id = -9006
    await _tutor_chat(chat_id, 1)
    payment = await dbm.add_payment(chat_id, "Март", 35000, TODAY)

    await dbm.set_payment_paid(chat_id, payment.id, True, paid_at="2024-03-10T10:00:00+00:00")
    stored = await dbm.get_payment_by_id(chat_id, payment.id)
    assert stored.is_paid is True and stored.paid_at

    await dbm.set_payment_paid(chat_id, payment.id, False, paid_at=None)
    stored = await dbm.get_payment_by_id(chat_id, payment.id)
    assert stored.is_paid is False and stored.paid_at is None


# --- Rights -------------------------------------------------------------------

async def test_only_owner_and_editor_may_change_payments(db):
    settings = build_test_settings()
    chat_id = -9007
    await _tutor_chat(chat_id, 9101)
    await dbm.set_access_mode(chat_id, perms.ACCESS_ROLES)
    await _member(chat_id, 9101, role="admin")
    await _member(chat_id, 9102, role="member", app_role=perms.ROLE_EDITOR)
    await _member(chat_id, 9103, role="member", app_role=perms.ROLE_STUDENT)

    body = {"title": "Март", "amount_minor": 35000, "due_date": "2024-03-10"}
    for user_id, expected in ((9101, 201), (9102, 201), (9103, 403)):
        async with web_client(settings) as (client, _s):
            await authenticate(client, user_id)
            resp = await client.post(f"/api/v1/classes/{chat_id}/payments", json=body)
            assert resp.status_code == expected, user_id


async def test_a_student_still_sees_what_has_to_be_paid(db):
    """Being told the amount is not the same as being able to change it."""
    settings = build_test_settings()
    chat_id = -9008
    await _tutor_chat(chat_id, 9201)
    await dbm.set_access_mode(chat_id, perms.ACCESS_ROLES)
    await _member(chat_id, 9202, role="member", app_role=perms.ROLE_STUDENT)
    await dbm.add_payment(chat_id, "Март", 35000, TODAY)

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9202)
        listed = (await client.get(f"/api/v1/classes/{chat_id}/payments")).json()
        assert listed[0]["amount_text"] == "350 UAH"
        assert listed[0]["can_edit"] is False


async def test_full_payment_lifecycle_over_http(db):
    settings = build_test_settings()
    chat_id = -9009
    await _tutor_chat(chat_id, 9301)
    await _member(chat_id, 9301, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9301)
        created = await client.post(
            f"/api/v1/classes/{chat_id}/payments",
            json={
                "title": "Занятия за март",
                "amount_minor": 125050,
                "currency": "UAH",
                "due_date": "2024-03-10",
                "period": "monthly",
                "remind_days_before": 2,
            },
        )
        assert created.status_code == 201
        body = created.json()
        pid = body["id"]
        assert body["amount_text"] == "1 250,50 UAH"
        assert body["period_label"] == "каждый месяц"

        edited = await client.patch(
            f"/api/v1/classes/{chat_id}/payments/{pid}", json={"amount_minor": 40000}
        )
        assert edited.status_code == 200
        assert edited.json()["amount_text"] == "400 UAH"

        paid = await client.patch(
            f"/api/v1/classes/{chat_id}/payments/{pid}/paid", json={"is_paid": True}
        )
        assert paid.status_code == 200
        assert paid.json()["is_paid"] is True and paid.json()["status"] == "paid"

        # Only unpaid entries when asked for those.
        assert (await client.get(f"/api/v1/classes/{chat_id}/payments?unpaid=true")).json() == []

        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/payments/{pid}")
        ).status_code == 204
        assert (
            await client.delete(f"/api/v1/classes/{chat_id}/payments/{pid}")
        ).status_code == 404

    entries = await dbm.get_audit_logs(chat_id, "payment")
    assert {e.action for e in entries} >= {"create", "update", "complete", "delete"}


async def test_bad_payment_input_is_rejected(db):
    settings = build_test_settings()
    chat_id = -9010
    await _tutor_chat(chat_id, 9401)
    await _member(chat_id, 9401, role="admin")

    async with web_client(settings) as (client, _s):
        await authenticate(client, 9401)
        for body in (
            {"title": "  ", "amount_minor": 100, "due_date": "2024-03-10"},
            {"title": "X", "amount_minor": -1, "due_date": "2024-03-10"},
            {"title": "X", "amount_minor": 100, "due_date": "2024-03-10", "period": "yearly"},
            {"title": "X", "amount_minor": 100, "due_date": "2024-03-10", "remind_days_before": 99},
        ):
            assert (
                await client.post(f"/api/v1/classes/{chat_id}/payments", json=body)
            ).status_code == 422, body


# --- Reminder -----------------------------------------------------------------

async def test_reminder_covers_overdue_and_due_soon_only(db):
    from services.scheduler import send_payment_reminder

    chat_id = -9011
    await _tutor_chat(chat_id, 1)
    await dbm.add_payment(chat_id, "Просрочено", 10000, TODAY - datetime.timedelta(days=2))
    await dbm.add_payment(chat_id, "Сегодня", 20000, TODAY, remind_days_before=0)
    await dbm.add_payment(chat_id, "Далеко", 30000, TODAY + datetime.timedelta(days=20))
    paid = await dbm.add_payment(chat_id, "Уже оплачено", 40000, TODAY)
    await dbm.set_payment_paid(chat_id, paid.id, True, paid_at=now_iso())

    bot = FakeBot()
    tz = __import__("pytz").timezone("Europe/Kyiv")
    assert await send_payment_reminder(bot, chat_id, tz, today=TODAY) is True

    text = "\n".join(sent[1] for sent in bot.sent)
    assert "Просрочено" in text
    assert "Сегодня" in text
    assert "Далеко" not in text
    assert "Уже оплачено" not in text


async def test_no_payments_means_no_message_at_all(db):
    from services.scheduler import send_payment_reminder

    chat_id = -9012
    await _tutor_chat(chat_id, 1)
    bot = FakeBot()
    tz = __import__("pytz").timezone("Europe/Kyiv")

    assert await send_payment_reminder(bot, chat_id, tz, today=TODAY) is True
    assert bot.sent == []


async def test_payment_reminder_is_sent_once_per_day(db):
    """Idempotent via its own outbox kind, like every other category."""
    from services.scheduler import send_payment_reminder

    chat_id = -9013
    await _tutor_chat(chat_id, 1)
    await dbm.add_payment(chat_id, "Март", 35000, TODAY, remind_days_before=0)
    bot = FakeBot()
    tz = __import__("pytz").timezone("Europe/Kyiv")

    assert await send_payment_reminder(bot, chat_id, tz, today=TODAY) is True
    assert await send_payment_reminder(bot, chat_id, tz, today=TODAY) is True
    assert len(bot.sent) == 1


async def test_today_shows_money_that_needs_attention(db):
    from handlers.today import format_today_message, get_today_data

    chat_id = -9014
    await _tutor_chat(chat_id, 1)
    await dbm.add_payment(chat_id, "Занятия за март", 35000, TODAY, remind_days_before=0)
    await dbm.add_payment(chat_id, "Далёкий платёж", 99900, TODAY + datetime.timedelta(days=30))

    text = format_today_message(await get_today_data(chat_id, TODAY), TODAY)
    assert "Об оплате" in text
    assert "Занятия за март" in text
    assert "Далёкий платёж" not in text
    # A tutor chat has no school timetable, so "Сегодня" does not talk about one.
    assert "Расписание на сегодня" not in text


async def test_today_in_a_class_chat_mentions_no_money(db):
    from handlers.today import format_today_message, get_today_data

    chat_id = -9015
    await dbm.get_or_create_chat(chat_id, "group")
    await dbm.finalize_onboarding(
        chat_id, "group", [(1, "08:00", "08:45")], {0: [(1, "Математика")]},
        profile=profiles.PROFILE_CLASS,
    )
    text = format_today_message(await get_today_data(chat_id, TODAY), TODAY)
    assert "Об оплате" not in text
    assert "Расписание на сегодня" in text


async def test_bot_payment_screen_refuses_a_student(db):
    from types import SimpleNamespace

    from handlers.payments import mark_paid
    from aiogram.fsm.context import FSMContext
    from aiogram.fsm.storage.base import StorageKey
    from aiogram.fsm.storage.memory import MemoryStorage

    chat_id, owner_id, student_id = -9016, 9501, 9502
    await _tutor_chat(chat_id, owner_id)
    await dbm.set_access_mode(chat_id, perms.ACCESS_ROLES)
    await _member(chat_id, student_id, role="admin", app_role=perms.ROLE_STUDENT)
    payment = await dbm.add_payment(chat_id, "Март", 35000, TODAY)

    class FakeCb:
        def __init__(self, user_id):
            self.message = SimpleNamespace(
                chat=SimpleNamespace(id=chat_id, type="group"), edit_text=None
            )
            self.data = f"pay_done:{payment.id}"
            self.bot = FakeBot(admins={student_id})
            self.from_user = SimpleNamespace(id=user_id, full_name="Ученик")
            self.alerts = []

        async def answer(self, *args, **kwargs):
            if kwargs.get("show_alert"):
                self.alerts.append(args[0] if args else None)

    state = FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=chat_id, user_id=student_id),
    )
    cb = FakeCb(student_id)
    await mark_paid(cb, state)

    assert cb.alerts, "a student must be told why the tap did nothing"
    assert (await dbm.get_payment_by_id(chat_id, payment.id)).is_paid is False


async def test_payments_are_a_tutor_only_feature(db):
    assert profiles.features(profiles.PROFILE_TUTOR).payments is True
    assert profiles.features(profiles.PROFILE_CLASS).payments is False
    assert profiles.features(profiles.PROFILE_PERSONAL).payments is False
