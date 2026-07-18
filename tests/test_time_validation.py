"""Fix #4: lesson time interval parsing and validation."""
import pytest

from utils import parse_time_interval, validate_against_previous


def test_normalizes_to_hh_mm():
    assert parse_time_interval("8:00 - 9:30") == ("08:00", "09:30")
    assert parse_time_interval("08:30-09:15") == ("08:30", "09:15")
    assert parse_time_interval("  10:00  -  10:45 ") == ("10:00", "10:45")


def test_reversed_interval_rejected():
    with pytest.raises(ValueError):
        parse_time_interval("12:00 - 08:00")


def test_zero_length_interval_rejected():
    with pytest.raises(ValueError):
        parse_time_interval("09:00 - 09:00")


def test_bad_format_rejected():
    for bad in ["", "abc", "25:00 - 26:00", "08:70 - 09:00", "0800 - 0900", None]:
        with pytest.raises(ValueError):
            parse_time_interval(bad)


def test_overlapping_lessons_rejected():
    # Previous lesson ends 09:15; a new lesson starting 09:00 overlaps.
    with pytest.raises(ValueError):
        validate_against_previous("09:00", "09:15")


def test_non_overlapping_lessons_ok():
    # Back-to-back (start == prev end) is allowed; later start is fine.
    validate_against_previous("09:15", "09:15")
    validate_against_previous("09:25", "09:15")
    validate_against_previous("08:00", None)  # first lesson, no previous
