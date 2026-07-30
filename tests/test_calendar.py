"""Tests for the reusable inline calendar widget (keyboards/calendar.py)."""
import datetime

from keyboards.calendar import (
    build_calendar, month_token, parse_month, shift_month, NOOP,
)

TODAY = datetime.date(2026, 9, 15)


def _all_buttons(kb):
    return [b for row in kb.inline_keyboard for b in row]


def _pick_dates(kb):
    return [
        b.callback_data.split(":", 1)[1]
        for b in _all_buttons(kb)
        if b.callback_data.startswith("hwa_date:")
    ]


def test_month_token_and_parse_round_trip():
    assert month_token(2026, 9) == "2026-09"
    assert parse_month("2026-09") == (2026, 9)


def test_parse_month_rejects_garbage():
    for bad in (None, "", "2026", "2026-13", "2026-00", "abc-09", "2026-9-1", "0-1"):
        assert parse_month(bad) is None, bad


def test_shift_month_rolls_the_year():
    assert shift_month(2026, 12, 1) == (2027, 1)
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 9, 3) == (2026, 12)


def test_callback_data_is_within_telegram_limit():
    kb = build_calendar(
        2026, 9, pick_prefix="hwa_date", nav_prefix="hwa_cal",
        today=TODAY, min_date=TODAY, cancel_cb="hw_list_active",
    )
    for button in _all_buttons(kb):
        # Telegram hard-limits callback_data to 64 bytes.
        assert len(button.callback_data.encode("utf-8")) <= 64, button.callback_data


def test_today_is_highlighted():
    kb = build_calendar(
        2026, 9, pick_prefix="hwa_date", nav_prefix="hwa_cal",
        today=TODAY, min_date=TODAY,
    )
    labels = [b.text for b in _all_buttons(kb)]
    assert "[15]" in labels
    # A non-today, in-range day is rendered plainly.
    assert "16" in labels


def test_navigation_targets_neighbouring_months():
    kb = build_calendar(
        2026, 9, pick_prefix="hwa_date", nav_prefix="hwa_cal",
        today=TODAY,
    )
    navs = [b.callback_data for b in _all_buttons(kb) if b.callback_data.startswith("hwa_cal:")]
    assert "hwa_cal:2026-08" in navs
    assert "hwa_cal:2026-10" in navs


def test_days_before_min_date_are_inert():
    kb = build_calendar(
        2026, 9, pick_prefix="hwa_date", nav_prefix="hwa_cal",
        today=TODAY, min_date=TODAY,
    )
    picks = _pick_dates(kb)
    # Sep 1..14 are before the 15th and must not be pickable.
    assert "2026-09-01" not in picks
    assert "2026-09-14" not in picks
    assert "2026-09-15" in picks
    assert "2026-09-30" in picks


def test_prev_arrow_hidden_when_whole_month_is_before_min():
    # Viewing the min_date's month: the previous month is entirely in the past,
    # so the ‹ arrow degrades to an inert cell rather than paging into a dead zone.
    kb = build_calendar(
        2026, 9, pick_prefix="hwa_date", nav_prefix="hwa_cal",
        today=TODAY, min_date=datetime.date(2026, 9, 1),
    )
    navs = [b.callback_data for b in _all_buttons(kb) if b.callback_data.startswith("hwa_cal:")]
    assert "hwa_cal:2026-08" not in navs
    assert "hwa_cal:2026-10" in navs


def test_days_after_max_date_are_inert():
    kb = build_calendar(
        2026, 9, pick_prefix="hwa_date", nav_prefix="hwa_cal",
        today=TODAY, max_date=datetime.date(2026, 9, 20),
    )
    picks = _pick_dates(kb)
    assert "2026-09-20" in picks
    assert "2026-09-21" not in picks


def test_labels_and_padding_are_noop():
    kb = build_calendar(
        2026, 9, pick_prefix="hwa_date", nav_prefix="hwa_cal", today=TODAY,
    )
    # Weekday labels row is entirely inert.
    weekday_row = kb.inline_keyboard[1]
    assert all(b.callback_data == NOOP for b in weekday_row)
    assert [b.text for b in weekday_row] == ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
