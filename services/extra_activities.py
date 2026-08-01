"""Pure filtering helpers for extra activities (clubs / tutors / sections).

Extracted from ``handlers/extra.py`` so the domain rule "which activities apply
on this date / weekday" lives in the service layer and can be reused by the
Telegram handlers, the reminder scheduler *and* the web API without any of them
importing a Telegram adapter. These functions never touch the DB or network.
"""
import datetime
from typing import List

from database.models import ExtraActivity


def activities_on_date(
    activities: List[ExtraActivity], date: datetime.date
) -> List[ExtraActivity]:
    """Activities that apply on a concrete ``date`` (weekly by weekday + once by date)."""
    weekday = date.weekday()
    matched = [
        a for a in activities
        if (a.kind == "weekly" and a.day_of_week == weekday)
        or (a.kind == "once" and a.activity_date == date)
    ]
    return sorted(matched, key=lambda a: a.start_time)


def activities_for_weekday(
    activities: List[ExtraActivity], day_of_week: int, today: datetime.date
) -> List[ExtraActivity]:
    """
    Activities for a weekday view: all weekly ones on ``day_of_week`` plus any
    upcoming (today-or-later) one-off activities whose date falls on that
    weekday, so a dated activity still surfaces on the right day tab.
    """
    matched = []
    for a in activities:
        if a.kind == "weekly" and a.day_of_week == day_of_week:
            matched.append(a)
        elif (
            a.kind == "once"
            and a.activity_date is not None
            and a.activity_date >= today
            and a.activity_date.weekday() == day_of_week
        ):
            matched.append(a)
    return sorted(matched, key=lambda a: a.start_time)
