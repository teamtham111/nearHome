"""Resolve weekday/weekend + local time to next valid future timestamp in Asia/Singapore."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.enums import DayType

SGT = ZoneInfo("Asia/Singapore")


def resolve_departure_timestamp(
    day_type: DayType,
    departure_time: time,
    from_date: date | None = None,
) -> datetime:
    """Return the next occurrence of the requested day type and time."""
    today = from_date or datetime.now(SGT).date()
    candidate = datetime.combine(today, departure_time, tzinfo=SGT)

    if day_type == DayType.WEEKDAY:
        # Move to next weekday (Mon-Fri)
        while candidate.weekday() >= 5:  # Sat=5, Sun=6
            candidate += timedelta(days=1)
    else:
        # Move to next weekend day
        while candidate.weekday() < 5:
            candidate += timedelta(days=1)

    now = datetime.now(SGT)
    if candidate <= now:
        candidate += timedelta(days=7 if day_type == DayType.WEEKDAY else 7)

    return candidate
