"""Shared departure-time helpers for traffic-aware routing requests.

Both Public Transport (personal journeys) and Driving components need a
concrete future departure time to ask Google for traffic-aware durations.
Centralised here so the "AM peak" / "off-peak" hour definitions live in one
place instead of being duplicated across engines.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SINGAPORE_TZ = ZoneInfo("Asia/Singapore")


def next_occurrence_at_hour(hour: int, minute: int = 0) -> datetime:
    """Next future datetime (Singapore time) at the given local hour.

    Google's Routes API requires `departureTime` to be in the future, so a
    same-day "AM peak" request made at 9pm must roll over to tomorrow.
    """
    now = datetime.now(tz=SINGAPORE_TZ)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
