from __future__ import annotations

from datetime import date

import pytest

from config.common import UNVERIFIED_LABEL
from config.trading_calendar import CalendarCoverageError, TradingCalendar


def test_repo_calendar_is_unverified(calendar: TradingCalendar) -> None:
    assert calendar.verified is False
    assert calendar.label == UNVERIFIED_LABEL


def test_weekend_and_holiday_are_not_trading_days(calendar: TradingCalendar) -> None:
    assert calendar.is_trading_day(date(2026, 3, 14)) is False  # Sabtu
    assert calendar.is_trading_day(date(2026, 3, 15)) is False  # Minggu
    assert calendar.is_trading_day(date(2026, 8, 17)) is False  # libur contoh
    assert calendar.is_trading_day(date(2026, 3, 16)) is True


def test_out_of_coverage_is_never_a_trading_day(calendar: TradingCalendar) -> None:
    with pytest.raises(CalendarCoverageError):
        calendar.is_trading_day(date(2027, 1, 4))
    with pytest.raises(CalendarCoverageError):
        calendar.previous_trading_session(date(2027, 1, 4))


def test_previous_trading_session_skips_weekend_and_holiday() -> None:
    cal = TradingCalendar.model_validate(
        {
            "meta": {},
            "coverage": {"start": "2026-03-01", "end": "2026-03-31"},
            "holidays": [{"date": "2026-03-16", "name": "libur uji"}],
        }
    )
    assert cal.previous_trading_session(date(2026, 3, 16)) == date(
        2026, 3, 13
    )  # Senin(libur) -> Jumat
    assert cal.previous_trading_session(date(2026, 3, 17)) == date(
        2026, 3, 13
    )  # Selasa -> lewati libur Senin
    assert cal.previous_trading_session(date(2026, 3, 13)) == date(2026, 3, 12)
    assert cal.next_trading_day(date(2026, 3, 13)) == date(2026, 3, 17)


def test_previous_session_walking_out_of_coverage_raises() -> None:
    cal = TradingCalendar.model_validate(
        {"meta": {}, "coverage": {"start": "2026-03-02", "end": "2026-03-31"}}
    )
    with pytest.raises(CalendarCoverageError):
        cal.previous_trading_session(date(2026, 3, 2))


def test_trading_days_range(calendar: TradingCalendar) -> None:
    days = calendar.trading_days(date(2026, 3, 9), date(2026, 3, 15))
    assert days == [
        date(2026, 3, 9),
        date(2026, 3, 10),
        date(2026, 3, 11),
        date(2026, 3, 12),
        date(2026, 3, 13),
    ]


def test_invalid_calendar_definitions() -> None:
    with pytest.raises(ValueError, match="luar cakupan"):
        TradingCalendar.model_validate(
            {
                "meta": {},
                "coverage": {"start": "2026-01-01", "end": "2026-01-31"},
                "holidays": [{"date": "2026-02-01"}],
            }
        )
    with pytest.raises(ValueError, match="duplikat"):
        TradingCalendar.model_validate(
            {
                "meta": {},
                "coverage": {"start": "2026-01-01", "end": "2026-01-31"},
                "holidays": [{"date": "2026-01-05"}, {"date": "2026-01-05"}],
            }
        )
    with pytest.raises(ValueError):
        TradingCalendar.model_validate(
            {"meta": {}, "coverage": {"start": "2026-02-01", "end": "2026-01-01"}}
        )
