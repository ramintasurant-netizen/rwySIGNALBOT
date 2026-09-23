"""Kontrak provider data pasar dan tipe hasil.

Hasil provider adalah *tagged union* ``Ok | Unsupported | Unavailable | Failed`` sehingga
"tidak didukung", "data tidak tersedia", "request gagal", dan "nilai nol yang sah" tidak
pernah tertukar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Literal

import pandas as pd

from core.redaction import redact
from core.timeutil import WIB, to_utc


class Capability(StrEnum):
    OHLCV_DAILY = "ohlcv_daily"
    OHLCV_INTRADAY = "ohlcv_intraday"
    QUOTE = "quote"
    FOREIGN_FLOW = "foreign_flow"
    BROKER_SUMMARY = "broker_summary"


class Timeframe(StrEnum):
    D1 = "1d"
    H1 = "1h"
    M15 = "15m"
    M5 = "5m"
    M1 = "1m"

    @property
    def is_intraday(self) -> bool:
        return self is not Timeframe.D1

    @property
    def duration(self) -> timedelta:
        return {
            Timeframe.D1: timedelta(days=1),
            Timeframe.H1: timedelta(hours=1),
            Timeframe.M15: timedelta(minutes=15),
            Timeframe.M5: timedelta(minutes=5),
            Timeframe.M1: timedelta(minutes=1),
        }[self]

    @property
    def capability(self) -> Capability:
        return Capability.OHLCV_INTRADAY if self.is_intraday else Capability.OHLCV_DAILY


class PriceBasis(StrEnum):
    RAW = "raw"
    ADJUSTED = "adjusted"


class QualityStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"  # valid tetapi tanpa cross-validation
    STALE = "stale"
    SUSPECT = "suspect"
    MISSING = "missing"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"


class DataOrigin(StrEnum):
    LIVE = "live"
    FIXTURE = "fixture"


class ProviderConfigurationError(RuntimeError):
    """Provider diminta aktif tanpa kontrak/konfigurasi yang sah."""


@dataclass(frozen=True, slots=True)
class ProviderMeta:
    provider: str
    fetched_at: datetime
    market_time: datetime | None = None
    origin: DataOrigin = DataOrigin.LIVE
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T
    meta: ProviderMeta


@dataclass(frozen=True, slots=True)
class Unsupported:
    provider: str
    reason: str


@dataclass(frozen=True, slots=True)
class Unavailable:
    provider: str
    reason: str


@dataclass(frozen=True, slots=True)
class Failed:
    provider: str
    error: str
    retryable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "error", redact(self.error))


type ProviderResult[T] = Ok[T] | Unsupported | Unavailable | Failed

OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")
REQUIRED_FRAME_COLUMNS: tuple[str, ...] = (*OHLCV_COLUMNS, "complete", "session_date")


@dataclass(frozen=True, slots=True)
class CorporateAction:
    session_date: date
    kind: Literal["dividend", "split"]
    value: Decimal


@dataclass(frozen=True, slots=True)
class OHLCVFrame:
    """OHLCV ternormalisasi.

    ``frame``: index ``DatetimeIndex`` tz-aware UTC terurut naik tanpa duplikat; kolom
    ``open, high, low, close, volume`` (float64), opsional ``value``, ``complete`` (bool),
    ``session_date`` (tanggal sesi WIB).
    """

    symbol: str
    timeframe: Timeframe
    price_basis: PriceBasis
    provider: str
    fetched_at: datetime
    frame: pd.DataFrame = field(repr=False)
    origin: DataOrigin = DataOrigin.LIVE
    corporate_actions: tuple[CorporateAction, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        df = self.frame
        missing = [c for c in REQUIRED_FRAME_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"kolom OHLCVFrame hilang: {missing}")
        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("index OHLCVFrame harus DatetimeIndex")
        if df.index.tz is None or str(df.index.tz) != "UTC":
            raise ValueError("index OHLCVFrame harus tz-aware UTC")
        if not df.index.is_monotonic_increasing:
            raise ValueError("index OHLCVFrame harus terurut naik")
        if self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at harus tz-aware")

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def last_bar_time(self) -> datetime | None:
        if self.frame.empty:
            return None
        return self.frame.index[-1].to_pydatetime()

    def complete_only(self) -> OHLCVFrame:
        mask = self.frame["complete"].astype(bool)
        return OHLCVFrame(
            symbol=self.symbol,
            timeframe=self.timeframe,
            price_basis=self.price_basis,
            provider=self.provider,
            fetched_at=self.fetched_at,
            frame=self.frame.loc[mask],
            origin=self.origin,
            corporate_actions=self.corporate_actions,
            notes=self.notes,
        )

    @property
    def last_complete_session(self) -> date | None:
        complete = self.frame.loc[self.frame["complete"].astype(bool)]
        if complete.empty:
            return None
        return complete["session_date"].iloc[-1]

    def close_on(self, session_date: date) -> Decimal | None:
        rows = self.frame.loc[self.frame["session_date"] == session_date]
        if rows.empty:
            return None
        return Decimal(repr(float(rows["close"].iloc[-1])))


@dataclass(frozen=True, slots=True)
class Quote:
    symbol: str
    price: Decimal
    market_time: datetime
    fetched_at: datetime
    provider: str
    prev_close: Decimal | None = None
    open: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    volume: int | None = None
    price_basis: PriceBasis = PriceBasis.RAW
    origin: DataOrigin = DataOrigin.LIVE
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ForeignFlow:
    symbol: str
    session_date: date
    net_value: Decimal  # nol yang sah tetap Decimal("0"), bukan None
    provider: str
    fetched_at: datetime
    buy_value: Decimal | None = None
    sell_value: Decimal | None = None
    origin: DataOrigin = DataOrigin.LIVE


@dataclass(frozen=True, slots=True)
class BrokerEntry:
    code: str
    buy_value: Decimal
    sell_value: Decimal

    @property
    def net_value(self) -> Decimal:
        return self.buy_value - self.sell_value


@dataclass(frozen=True, slots=True)
class BrokerSummary:
    symbol: str
    session_date: date
    entries: tuple[BrokerEntry, ...]
    provider: str
    fetched_at: datetime
    origin: DataOrigin = DataOrigin.LIVE


class MarketDataProvider(ABC):
    """Kontrak provider. Implementasi wajib non-blocking terhadap event loop."""

    name: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]] = frozenset()

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    @abstractmethod
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
        *,
        price_basis: PriceBasis = PriceBasis.RAW,
    ) -> ProviderResult[OHLCVFrame]: ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> ProviderResult[Quote]: ...

    async def get_foreign_flow(
        self, symbol: str, session_date: date
    ) -> ProviderResult[ForeignFlow]:
        return Unsupported(self.name, "provider tidak menyediakan data foreign flow")

    async def get_broker_summary(
        self, symbol: str, session_date: date
    ) -> ProviderResult[BrokerSummary]:
        return Unsupported(self.name, "provider tidak menyediakan broker summary")

    @abstractmethod
    async def health_check(self) -> bool: ...


# ----------------------------------------------------------------------------- normalisasi

_COLUMN_ALIASES = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "volume": "volume",
    "adj close": "adj_close",
    "adj_close": "adj_close",
    "adjclose": "adj_close",
    "value": "value",
    "dividends": "dividends",
    "stock splits": "splits",
    "stock_splits": "splits",
}


def normalize_ohlcv(
    raw: pd.DataFrame,
    *,
    symbol: str,
    timeframe: Timeframe,
    provider: str,
    fetched_at: datetime,
    price_basis: PriceBasis = PriceBasis.RAW,
    source_tz=WIB,
    is_daily_complete=None,
    now: datetime | None = None,
    origin: DataOrigin = DataOrigin.LIVE,
    notes: tuple[str, ...] = (),
) -> OHLCVFrame:
    """Ubah DataFrame gaya provider menjadi ``OHLCVFrame``.

    - Kolom dipetakan case-insensitive (``Adj Close`` → ``adj_close``).
    - Index naive dianggap ``source_tz``; hasil disimpan UTC.
    - ``price_basis=ADJUSTED`` menurunkan OHLC dari faktor ``adj_close/close`` dan
      menghasilkan objek terpisah; seri raw tidak pernah diubah diam-diam.
    - ``is_daily_complete(session_date, now) -> bool`` menentukan bar harian final;
      default: sesi sebelum tanggal WIB hari ini.
    """
    if raw is None or raw.empty:
        raise ValueError("DataFrame kosong")
    now = now or fetched_at
    df = raw.copy()
    df.columns = [
        _COLUMN_ALIASES.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns
    ]
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"kolom OHLCV hilang dari provider: {missing}")

    index = pd.DatetimeIndex(df.index)
    if index.tz is None:
        index = index.tz_localize(source_tz)
    index = index.tz_convert(UTC)
    df.index = index
    df = df.sort_index()
    if df.index.has_duplicates:
        notes = (*notes, "index memuat timestamp duplikat")  # validator kualitas menandai INVALID

    out = pd.DataFrame(index=df.index)
    for col in OHLCV_COLUMNS:
        out[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    if "value" in df.columns:
        out["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")

    if price_basis is PriceBasis.ADJUSTED:
        if "adj_close" not in df.columns:
            raise ValueError("seri adjusted diminta tetapi provider tidak memberi adj_close")
        factor = pd.to_numeric(df["adj_close"], errors="coerce").astype("float64") / out["close"]
        for col in ("open", "high", "low", "close"):
            out[col] = out[col] * factor
        notes = (*notes, "OHLC diturunkan dari faktor adj_close/close")

    wib_index = out.index.tz_convert(WIB)
    session_dates = [ts.date() for ts in wib_index]
    out["session_date"] = pd.Series(session_dates, index=out.index, dtype="object")

    if timeframe.is_intraday:
        bar_end = out.index + timeframe.duration
        out["complete"] = bar_end <= pd.Timestamp(to_utc(now))
    else:
        today_wib = to_utc(now).astimezone(WIB).date()
        if is_daily_complete is None:
            out["complete"] = [d < today_wib for d in session_dates]
        else:
            out["complete"] = [bool(is_daily_complete(d, now)) for d in session_dates]
    out["complete"] = out["complete"].astype(bool)

    actions: list[CorporateAction] = []
    if "dividends" in df.columns:
        div = pd.to_numeric(df["dividends"], errors="coerce").fillna(0.0)
        for ts, value in div[div > 0].items():
            actions.append(
                CorporateAction(ts.tz_convert(WIB).date(), "dividend", Decimal(repr(float(value))))
            )
    if "splits" in df.columns:
        spl = pd.to_numeric(df["splits"], errors="coerce").fillna(0.0)
        for ts, value in spl[spl > 0].items():
            actions.append(
                CorporateAction(ts.tz_convert(WIB).date(), "split", Decimal(repr(float(value))))
            )

    return OHLCVFrame(
        symbol=symbol,
        timeframe=timeframe,
        price_basis=price_basis,
        provider=provider,
        fetched_at=to_utc(fetched_at),
        frame=out,
        origin=origin,
        corporate_actions=tuple(actions),
        notes=notes,
    )
