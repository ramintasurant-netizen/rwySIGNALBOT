"""Aturan perdagangan BEI dari YAML: lot, fraksi harga, ARA/ARB, sesi, likuiditas.

Engine tidak boleh menghardcode nilai-nilai ini. Nilai dengan ``meta.verified: false``
dilabeli CONTOH / BELUM TERVERIFIKASI dan memblokir mode produksi.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config.common import ConfigError, VerificationMeta, canonical_symbol, load_yaml
from core.timeutil import WIB, to_wib, wib_datetime


class TickBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_price: Decimal = Field(ge=0)
    max_price: Decimal | None = Field(default=None, gt=0)
    tick: Decimal = Field(gt=0)

    def contains(self, price: Decimal) -> bool:
        return price >= self.min_price and (self.max_price is None or price < self.max_price)


class PriceLimitBand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_price: Decimal = Field(ge=0)
    max_price: Decimal | None = Field(default=None, gt=0)
    up_pct: Decimal = Field(gt=0, le=100)
    down_pct: Decimal = Field(gt=0, le=100)

    def contains(self, price: Decimal) -> bool:
        return price >= self.min_price and (self.max_price is None or price < self.max_price)


class PriceLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reference_price: Literal["previous_close"] = "previous_close"
    min_price: Decimal = Field(gt=0)
    bands: list[PriceLimitBand] = Field(min_length=1)

    @field_validator("bands")
    @classmethod
    def _bands_contiguous(cls, bands: list[PriceLimitBand]) -> list[PriceLimitBand]:
        _check_contiguous([(b.min_price, b.max_price) for b in bands], "price_limits.bands")
        return bands


class SessionWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: time
    end: time

    @model_validator(mode="after")
    def _ordered(self) -> SessionWindow:
        if self.end <= self.start:
            raise ValueError(f"sesi tidak valid: end {self.end} <= start {self.start}")
        return self


class DaySchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pre_opening: SessionWindow
    session_1: SessionWindow
    session_2: SessionWindow
    pre_closing: SessionWindow
    post_trading: SessionWindow | None = None

    @model_validator(mode="after")
    def _ordered(self) -> DaySchedule:
        windows = [self.pre_opening, self.session_1, self.session_2, self.pre_closing]
        if self.post_trading is not None:
            windows.append(self.post_trading)
        for earlier, later in zip(windows, windows[1:], strict=False):
            if later.start < earlier.end:
                raise ValueError("jendela sesi harus berurutan dan tidak tumpang tindih")
        return self

    @property
    def last_window(self) -> SessionWindow:
        return self.post_trading or self.pre_closing

    @property
    def continuous_trading_end(self) -> time:
        return self.session_2.end


class Sessions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    regular: DaySchedule
    friday: DaySchedule


class Liquidity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_avg_daily_value_idr: Decimal = Field(ge=0)
    min_avg_daily_volume_shares: int = Field(ge=0)
    lookback_days: int = Field(ge=1, le=250)


class Exclusions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suspended_symbols: list[str] = []
    special_monitoring_board_symbols: list[str] = []

    @field_validator("suspended_symbols", "special_monitoring_board_symbols")
    @classmethod
    def _canonical(cls, symbols: list[str]) -> list[str]:
        return [canonical_symbol(s) for s in symbols]

    def is_excluded(self, symbol: str) -> bool:
        sym = canonical_symbol(symbol)
        return sym in self.suspended_symbols or sym in self.special_monitoring_board_symbols


class MarketRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    meta: VerificationMeta
    lot_size: int = Field(gt=0)
    tick_table: list[TickBand] = Field(min_length=1)
    price_limits: PriceLimits
    sessions: Sessions
    liquidity: Liquidity
    exclusions: Exclusions = Exclusions()
    # Bar harian dianggap final setelah jendela terakhir + buffer (post-trading menambah volume).
    daily_bar_completion_buffer_minutes: int = Field(default=15, ge=0, le=180)

    @field_validator("tick_table")
    @classmethod
    def _ticks_contiguous(cls, bands: list[TickBand]) -> list[TickBand]:
        _check_contiguous([(b.min_price, b.max_price) for b in bands], "tick_table")
        return bands

    @property
    def verified(self) -> bool:
        return self.meta.verified

    @property
    def label(self) -> str:
        return self.meta.display_label

    def tick_for(self, price: Decimal) -> Decimal:
        for band in self.tick_table:
            if band.contains(price):
                return band.tick
        raise ValueError(f"harga {price} di luar tabel fraksi harga")

    def price_limit_band_for(self, reference_price: Decimal) -> PriceLimitBand:
        for band in self.price_limits.bands:
            if band.contains(reference_price):
                return band
        raise ValueError(f"harga referensi {reference_price} di luar tabel ARA/ARB")

    def schedule_for(self, d: date) -> DaySchedule:
        return self.sessions.friday if d.isoweekday() == 5 else self.sessions.regular

    def daily_bar_final_time(self, session_date: date) -> datetime:
        schedule = self.schedule_for(session_date)
        end = wib_datetime(session_date, schedule.last_window.end)
        return end + timedelta(minutes=self.daily_bar_completion_buffer_minutes)

    def is_daily_bar_complete(self, session_date: date, now: datetime) -> bool:
        now_wib = to_wib(now)
        if session_date < now_wib.date():
            return True
        if session_date > now_wib.date():
            return False
        return now_wib >= self.daily_bar_final_time(session_date)

    def session_open_time(self, session_date: date) -> datetime:
        return wib_datetime(session_date, self.schedule_for(session_date).session_1.start)


def _check_contiguous(ranges: list[tuple[Decimal, Decimal | None]], name: str) -> None:
    if not ranges:
        raise ValueError(f"{name} kosong")
    for i, (lo, hi) in enumerate(ranges):
        if hi is not None and hi <= lo:
            raise ValueError(f"{name}[{i}]: max_price harus > min_price")
        if i + 1 < len(ranges):
            if hi is None:
                raise ValueError(f"{name}[{i}]: hanya rentang terakhir yang boleh tanpa max_price")
            if ranges[i + 1][0] != hi:
                raise ValueError(
                    f"{name}[{i + 1}]: min_price {ranges[i + 1][0]} harus sama dengan max_price sebelumnya {hi}"
                )
    if ranges[-1][1] is not None:
        raise ValueError(f"{name}: rentang terakhir harus terbuka (max_price: null)")


def load_market_rules(path: Path) -> MarketRules:
    try:
        return MarketRules.model_validate(load_yaml(path))
    except ValueError as exc:  # pydantic ValidationError adalah subclass ValueError
        raise ConfigError(f"market_rules tidak valid ({path}): {exc}") from exc


__all__ = [
    "WIB",
    "DaySchedule",
    "Exclusions",
    "Liquidity",
    "MarketRules",
    "PriceLimitBand",
    "PriceLimits",
    "SessionWindow",
    "Sessions",
    "TickBand",
    "load_market_rules",
]
