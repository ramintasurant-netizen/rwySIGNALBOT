"""Adapter Yahoo Finance (yfinance) — fallback development, bukan jaminan data real-time.

Catatan penting:
- Saham IDX memakai suffix ``.JK``; simbol kanonik internal tanpa suffix.
- yfinance sinkron dan berbasis jaringan: setiap panggilan dibungkus ``asyncio.to_thread``
  di balik semaphore dan timeout agar event loop tidak terblokir.
- ``Close`` Yahoo sudah disesuaikan untuk split (bukan dividen); hanya bar setelah aksi korporasi
  terakhir yang identik dengan harga transaksi sebenarnya. Dicatat pada ``notes``.
- Cakupan/delay data harian dan intraday ``.JK`` BELUM DIVERIFIKASI (docs/verification_required.md).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import ClassVar, Protocol

import pandas as pd

from config.common import canonical_symbol
from config.market_rules import MarketRules
from core.redaction import redact_exception
from core.timeutil import to_utc, utc_now
from data.providers.base import (
    Capability,
    Failed,
    MarketDataProvider,
    OHLCVFrame,
    Ok,
    PriceBasis,
    ProviderMeta,
    ProviderResult,
    Quote,
    Timeframe,
    Unavailable,
    normalize_ohlcv,
)

YAHOO_SUFFIX = ".JK"
_RAW_BASIS_NOTE = (
    "Yahoo: harga historis disesuaikan split (bukan dividen); bar terbaru = harga transaksi"
)
# Tanpa start/end, yfinance memakai period=1mo (terlalu pendek untuk warmup EMA200).
# Default eksplisit per timeframe agar histori pendek tidak lolos diam-diam.
DEFAULT_PERIODS: dict[Timeframe, str] = {
    Timeframe.D1: "2y",
    Timeframe.H1: "1mo",
    Timeframe.M15: "5d",
    Timeframe.M5: "5d",
    Timeframe.M1: "1d",
}


class Downloader(Protocol):
    """Fungsi sinkron pengambil histori gaya yfinance ``Ticker.history``."""

    def __call__(
        self,
        ticker: str,
        *,
        interval: str,
        start: datetime | None,
        end: datetime | None,
        period: str | None,
    ) -> pd.DataFrame: ...


def yfinance_downloader(
    ticker: str,
    *,
    interval: str,
    start: datetime | None,
    end: datetime | None,
    period: str | None,
) -> pd.DataFrame:
    import yfinance as yf  # impor malas: berat dan hanya dibutuhkan saat akses jaringan

    kwargs: dict[str, object] = {"interval": interval, "auto_adjust": False, "actions": True}
    if period:
        kwargs["period"] = period
    else:
        kwargs["start"] = start
        kwargs["end"] = end
    return yf.Ticker(ticker).history(**kwargs)


_INDEX_RE = re.compile(r"^\^[A-Z0-9]{2,10}$")


def _internal_symbol(symbol: str) -> str:
    raw = symbol.strip().upper()
    return raw if _INDEX_RE.match(raw) else canonical_symbol(symbol)


def to_yahoo_ticker(symbol: str) -> str:
    """Saham IDX → ``KODE.JK``; simbol indeks Yahoo (diawali ``^``, mis. ``^JKSE``) dipakai apa adanya."""
    raw = symbol.strip().upper()
    if _INDEX_RE.match(raw):
        return raw
    return f"{canonical_symbol(symbol)}{YAHOO_SUFFIX}"


_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


class YahooFinanceProvider(MarketDataProvider):
    name: ClassVar[str] = "yahoo"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.OHLCV_DAILY, Capability.OHLCV_INTRADAY, Capability.QUOTE}
    )

    def __init__(
        self,
        *,
        rules: MarketRules | None = None,
        max_concurrency: int = 4,
        timeout_seconds: float = 20.0,
        downloader: Downloader | None = None,
        clock: Callable[[], datetime] = utc_now,
        health_symbol: str = "BBCA",
    ) -> None:
        self._rules = rules
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._timeout = timeout_seconds
        self._download: Downloader = downloader or yfinance_downloader
        self._clock = clock
        self._health_symbol = health_symbol

    # ------------------------------------------------------------------ internal
    async def _fetch(
        self,
        ticker: str,
        *,
        interval: str,
        start: datetime | None = None,
        end: datetime | None = None,
        period: str | None = None,
    ) -> pd.DataFrame:
        async with self._semaphore:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._download, ticker, interval=interval, start=start, end=end, period=period
                ),
                timeout=self._timeout,
            )

    def _is_daily_complete(self):
        if self._rules is None:
            return None
        return self._rules.is_daily_bar_complete

    # ------------------------------------------------------------------ kontrak
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
        *,
        price_basis: PriceBasis = PriceBasis.RAW,
    ) -> ProviderResult[OHLCVFrame]:
        try:
            ticker = to_yahoo_ticker(symbol)
        except ValueError as exc:
            return Failed(self.name, redact_exception(exc), retryable=False)
        fetched_at = self._clock()
        period = DEFAULT_PERIODS[timeframe] if start is None and end is None else None
        try:
            raw = await self._fetch(
                ticker, interval=timeframe.value, start=start, end=end, period=period
            )
        except TimeoutError:
            return Failed(self.name, f"timeout {self._timeout}s mengambil {ticker}", retryable=True)
        except _RETRYABLE_ERRORS as exc:
            return Failed(self.name, redact_exception(exc), retryable=True)
        except Exception as exc:  # noqa: BLE001 - batas provider: semua error dipetakan ke Failed
            return Failed(self.name, redact_exception(exc), retryable=False)

        if raw is None or raw.empty:
            return Unavailable(
                self.name, f"Yahoo mengembalikan 0 bar untuk {ticker} ({timeframe.value})"
            )
        try:
            frame = normalize_ohlcv(
                raw,
                symbol=_internal_symbol(symbol),
                timeframe=timeframe,
                provider=self.name,
                fetched_at=fetched_at,
                price_basis=price_basis,
                is_daily_complete=self._is_daily_complete(),
                now=fetched_at,
                notes=(_RAW_BASIS_NOTE,) if price_basis is PriceBasis.RAW else (),
            )
        except ValueError as exc:
            return Failed(self.name, f"normalisasi gagal: {redact_exception(exc)}", retryable=False)
        return Ok(frame, ProviderMeta(self.name, to_utc(fetched_at), frame.last_bar_time))

    async def get_quote(self, symbol: str) -> ProviderResult[Quote]:
        try:
            ticker = to_yahoo_ticker(symbol)
        except ValueError as exc:
            return Failed(self.name, redact_exception(exc), retryable=False)
        fetched_at = self._clock()
        try:
            intraday = await self._fetch(ticker, interval="1m", period="1d")
        except TimeoutError:
            return Failed(
                self.name, f"timeout {self._timeout}s mengambil quote {ticker}", retryable=True
            )
        except _RETRYABLE_ERRORS as exc:
            return Failed(self.name, redact_exception(exc), retryable=True)
        except Exception as exc:  # noqa: BLE001
            return Failed(self.name, redact_exception(exc), retryable=False)
        if intraday is None or intraday.empty:
            return Unavailable(self.name, f"Yahoo tidak memberi bar intraday untuk {ticker}")

        try:
            bars = normalize_ohlcv(
                intraday,
                symbol=_internal_symbol(symbol),
                timeframe=Timeframe.M1,
                provider=self.name,
                fetched_at=fetched_at,
                now=fetched_at,
            )
        except ValueError as exc:
            return Failed(
                self.name, f"normalisasi quote gagal: {redact_exception(exc)}", retryable=False
            )
        df = bars.frame
        last = df.iloc[-1]
        market_time = df.index[-1].to_pydatetime()
        notes: list[str] = [
            "quote diturunkan dari bar 1 menit terakhir; delay Yahoo belum diverifikasi"
        ]

        prev_close: Decimal | None = None
        try:
            daily = await self._fetch(ticker, interval="1d", period="10d")
            if daily is not None and not daily.empty:
                dframe = normalize_ohlcv(
                    daily,
                    symbol=_internal_symbol(symbol),
                    timeframe=Timeframe.D1,
                    provider=self.name,
                    fetched_at=fetched_at,
                    is_daily_complete=self._is_daily_complete(),
                    now=fetched_at,
                )
                complete = dframe.complete_only().frame
                if not complete.empty:
                    prev_close = Decimal(repr(float(complete["close"].iloc[-1])))
        except Exception as exc:  # noqa: BLE001 - prev_close opsional; kegagalan dicatat, bukan dikarang
            notes.append(f"prev_close tidak tersedia: {redact_exception(exc)}")

        quote = Quote(
            symbol=_internal_symbol(symbol),
            price=Decimal(repr(float(last["close"]))),
            market_time=market_time,
            fetched_at=to_utc(fetched_at),
            provider=self.name,
            prev_close=prev_close,
            open=Decimal(repr(float(df["open"].iloc[0]))),
            high=Decimal(repr(float(df["high"].max()))),
            low=Decimal(repr(float(df["low"].min()))),
            volume=int(df["volume"].fillna(0).sum()),
            notes=tuple(notes),
        )
        return Ok(quote, ProviderMeta(self.name, to_utc(fetched_at), market_time))

    async def health_check(self) -> bool:
        try:
            raw = await self._fetch(
                to_yahoo_ticker(self._health_symbol), interval="1d", period="5d"
            )
        except Exception:  # noqa: BLE001
            return False
        return raw is not None and not raw.empty
