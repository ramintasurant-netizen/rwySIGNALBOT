"""Helper waktu. Semua datetime internal tz-aware; penyimpanan UTC, tampilan WIB."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_aware(dt: datetime, assume_tz: ZoneInfo = WIB) -> datetime:
    """Datetime naive dianggap berada di ``assume_tz``; datetime aware dibiarkan."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return dt.replace(tzinfo=assume_tz)
    return dt


def to_utc(dt: datetime) -> datetime:
    return ensure_aware(dt).astimezone(UTC)


def to_wib(dt: datetime) -> datetime:
    return ensure_aware(dt).astimezone(WIB)


def wib_date(dt: datetime) -> date:
    return to_wib(dt).date()


def wib_datetime(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=WIB)


def parse_hhmm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f"format jam harus HH:MM atau HH:MM:SS, diterima {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    return time(hour=hour, minute=minute, second=second)


def age(now: datetime, then: datetime) -> timedelta:
    return to_utc(now) - to_utc(then)
