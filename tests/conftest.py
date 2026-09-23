"""Fixture bersama. Semua data adalah SINTETIS berlabel origin=fixture; tidak ada akses jaringan."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from config.market_rules import MarketRules, load_market_rules
from config.settings import Settings
from config.trading_calendar import TradingCalendar, load_trading_calendar
from data.providers.base import (
    Capability,
    DataOrigin,
    Failed,
    ForeignFlow,
    MarketDataProvider,
    OHLCVFrame,
    Ok,
    PriceBasis,
    ProviderMeta,
    ProviderResult,
    Quote,
    Timeframe,
    Unavailable,
    Unsupported,
    normalize_ohlcv,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cegah variabel environment mesin pengembang/CI bocor ke Settings dalam test."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
        monkeypatch.delenv(name, raising=False)


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    return make_settings


@pytest.fixture(scope="session")
def market_rules() -> MarketRules:
    return load_market_rules(CONFIG_DIR / "market_rules.yaml")


@pytest.fixture(scope="session")
def calendar() -> TradingCalendar:
    return load_trading_calendar(CONFIG_DIR / "trading_calendar.yaml")


# ----------------------------------------------------------------------------- data sintetis


def make_yf_frame(
    n: int = 300,
    *,
    end_session: date = date(2026, 3, 13),
    tz: str | None = "Asia/Jakarta",
    seed: int = 1,
    with_adj_close: bool = True,
    start_price: float = 1000.0,
    intraday: bool = False,
) -> pd.DataFrame:
    """DataFrame gaya yfinance ``Ticker.history`` (kolom kapital) dari random walk deterministik."""
    rng = np.random.default_rng(seed)
    if intraday:
        start = datetime.combine(end_session, datetime.min.time()).replace(hour=9)
        index = pd.date_range(start=start, periods=n, freq="1min", tz=tz)
    else:
        index = pd.bdate_range(end=end_session, periods=n, tz=tz)
    close = start_price * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    close = np.round(close / 5) * 5
    open_ = np.round(close * (1 + rng.normal(0, 0.003, n)) / 5) * 5
    spread = np.abs(rng.normal(0, 0.005, n))
    high = np.round(np.maximum(open_, close) * (1 + spread) / 5) * 5
    low = np.round(np.minimum(open_, close) * (1 - spread) / 5) * 5
    volume = rng.integers(1_000_000, 5_000_000, n).astype("int64")
    df = pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
            "Dividends": 0.0,
            "Stock Splits": 0.0,
        },
        index=index,
    )
    if with_adj_close:
        df["Adj Close"] = df["Close"] * 0.98
    df.attrs["origin"] = DataOrigin.FIXTURE.value
    return df


def make_frame(
    symbol: str = "BBCA",
    *,
    n: int = 300,
    end_session: date = date(2026, 3, 13),
    provider: str = "fake",
    now: datetime | None = None,
    seed: int = 1,
    timeframe: Timeframe = Timeframe.D1,
    price_basis: PriceBasis = PriceBasis.RAW,
    start_price: float = 1000.0,
) -> OHLCVFrame:
    now = now or datetime(2026, 3, 16, 1, 0, tzinfo=UTC)
    raw = make_yf_frame(
        n,
        end_session=end_session,
        seed=seed,
        intraday=timeframe.is_intraday,
        start_price=start_price,
    )
    return normalize_ohlcv(
        raw,
        symbol=symbol,
        timeframe=timeframe,
        provider=provider,
        fetched_at=now,
        now=now,
        price_basis=price_basis,
        origin=DataOrigin.FIXTURE,
    )


# ----------------------------------------------------------------------------- provider palsu


@dataclass
class FakeProvider(MarketDataProvider):
    """Provider deterministik: hasil per pemanggilan dari antrean atau callable."""

    provider_name: str = "fake"
    ohlcv_results: (
        list[ProviderResult[OHLCVFrame]] | Callable[..., ProviderResult[OHLCVFrame]] | None
    ) = None
    quote_results: list[ProviderResult[Quote]] | None = None
    foreign_flow_result: ProviderResult[ForeignFlow] | None = None
    caps: frozenset[Capability] = frozenset(
        {Capability.OHLCV_DAILY, Capability.OHLCV_INTRADAY, Capability.QUOTE}
    )
    delay: float = 0.0
    raise_exc: BaseException | None = None
    healthy: bool = True
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.name = self.provider_name  # type: ignore[misc]
        self.capabilities = self.caps  # type: ignore[misc]

    async def _maybe_wait(self) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raise_exc is not None:
            raise self.raise_exc

    def _next(self, results: Any, *args: Any) -> Any:
        if callable(results):
            return results(*args)
        if isinstance(results, list):
            if not results:
                raise AssertionError(f"{self.name}: antrean hasil habis")
            return results.pop(0) if len(results) > 1 else results[0]
        return results

    async def get_ohlcv(self, symbol, timeframe, start, end, *, price_basis=PriceBasis.RAW):
        self.calls.append(("ohlcv", (symbol, timeframe, price_basis)))
        await self._maybe_wait()
        if self.ohlcv_results is None:
            return Unavailable(self.name, "tidak ada data fixture")
        return self._next(self.ohlcv_results, symbol, timeframe, price_basis)

    async def get_quote(self, symbol):
        self.calls.append(("quote", (symbol,)))
        await self._maybe_wait()
        if self.quote_results is None:
            return Unavailable(self.name, "tidak ada quote fixture")
        return self._next(self.quote_results)

    async def get_foreign_flow(self, symbol, session_date):
        self.calls.append(("foreign_flow", (symbol, session_date)))
        if Capability.FOREIGN_FLOW not in self.capabilities or self.foreign_flow_result is None:
            return Unsupported(self.name, "tidak didukung")
        return self.foreign_flow_result

    async def health_check(self) -> bool:
        return self.healthy

    def call_count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)


def ok_frame(provider: str, frame: OHLCVFrame) -> Ok[OHLCVFrame]:
    return Ok(
        frame, ProviderMeta(provider, frame.fetched_at, frame.last_bar_time, DataOrigin.FIXTURE)
    )


def failed(provider: str, *, retryable: bool = False) -> Failed:
    return Failed(provider, "kegagalan fixture", retryable=retryable)


def make_quote(
    provider: str,
    *,
    price: str = "9450",
    market_time: datetime,
    fetched_at: datetime | None = None,
    prev_close: str | None = "9400",
) -> Ok[Quote]:
    fetched_at = fetched_at or market_time
    quote = Quote(
        symbol="BBCA",
        price=Decimal(price),
        market_time=market_time,
        fetched_at=fetched_at,
        provider=provider,
        prev_close=Decimal(prev_close) if prev_close else None,
        origin=DataOrigin.FIXTURE,
    )
    return Ok(quote, ProviderMeta(provider, fetched_at, market_time, DataOrigin.FIXTURE))


class FakeClock:
    """Jam deterministik untuk aggregator/resilience: wall-clock UTC + monotonic + sleep palsu."""

    def __init__(self, start: datetime | None = None) -> None:
        self.now_utc = start or datetime(2026, 3, 16, 1, 0, tzinfo=UTC)
        self.mono = 1000.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self.now_utc

    def monotonic(self) -> float:
        return self.mono

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.now_utc = self.now_utc + pd.Timedelta(seconds=seconds).to_pytimedelta()

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()
