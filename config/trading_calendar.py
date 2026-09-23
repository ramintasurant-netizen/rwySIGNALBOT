"""Kalender perdagangan BEI dari YAML dengan cakupan tanggal eksplisit.

Tanggal di luar cakupan tidak pernah dianggap hari perdagangan secara diam-diam:
setiap pemeriksaan melempar ``CalendarCoverageError`` agar job produksi diblokir.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.common import ConfigError, VerificationMeta, load_yaml


class CalendarCoverageError(LookupError):
    """Tanggal berada di luar cakupan kalender yang dikonfigurasi."""


class Holiday(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    name: str = ""


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> Coverage:
        if self.end < self.start:
            raise ValueError("coverage.end harus >= coverage.start")
        return self


class TradingCalendar(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    meta: VerificationMeta
    coverage: Coverage
    # ISO weekday: 1=Senin ... 6=Sabtu, 7=Minggu
    weekend_days: list[int] = Field(default=[6, 7], min_length=0, max_length=6)
    holidays: list[Holiday] = []

    @model_validator(mode="after")
    def _validate(self) -> TradingCalendar:
        for wd in self.weekend_days:
            if not 1 <= wd <= 7:
                raise ValueError("weekend_days memakai ISO weekday 1..7")
        seen: set[date] = set()
        for holiday in self.holidays:
            if holiday.date in seen:
                raise ValueError(f"tanggal libur duplikat: {holiday.date}")
            seen.add(holiday.date)
            if not (self.coverage.start <= holiday.date <= self.coverage.end):
                raise ValueError(f"libur {holiday.date} berada di luar cakupan kalender")
        return self

    @property
    def verified(self) -> bool:
        return self.meta.verified

    @property
    def label(self) -> str:
        return self.meta.display_label

    @property
    def holiday_dates(self) -> frozenset[date]:
        return frozenset(h.date for h in self.holidays)

    def is_covered(self, d: date) -> bool:
        return self.coverage.start <= d <= self.coverage.end

    def require_covered(self, d: date) -> None:
        if not self.is_covered(d):
            raise CalendarCoverageError(
                f"tanggal {d} di luar cakupan kalender "
                f"({self.coverage.start}..{self.coverage.end}); perbarui trading_calendar.yaml"
            )

    def is_trading_day(self, d: date) -> bool:
        self.require_covered(d)
        return d.isoweekday() not in self.weekend_days and d not in self.holiday_dates

    def previous_trading_session(self, d: date) -> date:
        """Sesi perdagangan terakhir yang *sebelum* ``d`` (bukan sekadar d-1)."""
        self.require_covered(d)
        cursor = d - timedelta(days=1)
        while True:
            if self.is_trading_day(cursor):  # melempar CalendarCoverageError jika keluar cakupan
                return cursor
            cursor -= timedelta(days=1)

    def next_trading_day(self, d: date) -> date:
        self.require_covered(d)
        cursor = d + timedelta(days=1)
        while True:
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)

    def trading_days(self, start: date, end: date) -> list[date]:
        if end < start:
            raise ValueError("end harus >= start")
        self.require_covered(start)
        self.require_covered(end)
        out: list[date] = []
        cursor = start
        while cursor <= end:
            if self.is_trading_day(cursor):
                out.append(cursor)
            cursor += timedelta(days=1)
        return out


def load_trading_calendar(path: Path) -> TradingCalendar:
    try:
        return TradingCalendar.model_validate(load_yaml(path))
    except ValueError as exc:
        raise ConfigError(f"trading_calendar tidak valid ({path}): {exc}") from exc
