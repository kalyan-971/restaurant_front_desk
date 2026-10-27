"""Booking rules. Pure functions so they are easy to test.

The AI never decides whether a table is available -- this code does.
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .config import TZ


@dataclass
class Check:
    ok: bool
    reason: str = ""


def _to_time(value) -> time:
    if isinstance(value, time):
        return value
    parts = str(value).split(":")
    return time(int(parts[0]), int(parts[1]))


def slot_start(dt: datetime, slot_minutes: int) -> datetime:
    """Round a local datetime down to the start of its slot."""
    minutes = (dt.hour * 60 + dt.minute) // slot_minutes * slot_minutes
    return dt.replace(hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)


def time_options(restaurant: dict) -> list[str]:
    """Times shown on the booking page, e.g. ['17:00', '17:30', ...]."""
    step = int(restaurant.get("slot_minutes", 30))
    start = _to_time(restaurant.get("open_time", "17:00"))
    end = _to_time(restaurant.get("last_booking_time", "21:00"))
    out, t = [], datetime.combine(date(2000, 1, 1), start)
    last = datetime.combine(date(2000, 1, 1), end)
    while t <= last:
        out.append(t.strftime("%H:%M"))
        t += timedelta(minutes=step)
    return out


def check_availability(restaurant: dict, starts_at: datetime, party_size: int,
                       existing: list[dict], now: datetime) -> Check:
    """Can this booking be accepted?

    starts_at and now must be timezone-aware. `existing` is the restaurant's
    bookings for that day (any status); only confirmed ones count.
    """
    local = starts_at.astimezone(TZ)
    if party_size < 1:
        return Check(False, "Party size must be at least 1.")
    if party_size > int(restaurant.get("max_online_party", 7)):
        return Check(False, "For bigger groups please call or text us and the team will sort it out.")
    if starts_at <= now:
        return Check(False, "That time has already passed.")
    if local.weekday() in (restaurant.get("closed_days") or []):
        return Check(False, "Sorry, we're closed that day.")
    if local.strftime("%H:%M") not in time_options(restaurant):
        return Check(False, "Please pick one of the listed times.")

    step = int(restaurant.get("slot_minutes", 30))
    start = slot_start(local, step)
    end = start + timedelta(minutes=step)
    booked = sum(
        int(b["party_size"]) for b in existing
        if b.get("status", "confirmed") == "confirmed"
        and start <= _parse(b["starts_at"]).astimezone(TZ) < end
    )
    if booked + party_size > int(restaurant.get("max_covers_per_slot", 40)):
        return Check(False, "Sorry, that time is fully booked. Please try another time.")
    return Check(True)


def _parse(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def normalize_au_mobile(raw: str) -> str | None:
    """'0412 345 678' or '+61 412 345 678' -> '+61412345678'. None if not an AU mobile."""
    digits = re.sub(r"[^\d+]", "", raw or "")
    if digits.startswith("+61"):
        digits = "0" + digits[3:]
    elif digits.startswith("61") and len(digits) == 11:
        digits = "0" + digits[2:]
    if re.fullmatch(r"04\d{8}", digits):
        return "+61" + digits[1:]
    return None
