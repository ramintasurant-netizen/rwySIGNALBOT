"""Fixture harga SINTETIS deterministik untuk engine (origin=fixture). Bukan data pasar."""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from data.providers.base import DataOrigin, OHLCVFrame, Timeframe, normalize_ohlcv

END_SESSION = date(2026, 3, 13)  # Jumat
NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)  # Senin 08:00 WIB


def _frame_from_close(
    close: np.ndarray,
    *,
    symbol: str,
    volume: np.ndarray | None = None,
    high: np.ndarray | None = None,
    low: np.ndarray | None = None,
    open_: np.ndarray | None = None,
    end_session: date = END_SESSION,
    provider: str = "fixture",
    now: datetime = NOW,
) -> OHLCVFrame:
    n = len(close)
    close = np.round(close / 5) * 5
    if open_ is None:
        open_ = np.concatenate([[close[0]], close[:-1]])
    if high is None:
        high = np.maximum(open_, close) + 5
    if low is None:
        low = np.minimum(open_, close) - 5
    if volume is None:
        volume = np.full(n, 8_000_000.0)
    index = pd.bdate_range(end=end_session, periods=n, tz="Asia/Jakarta")
    raw = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=index
    )
    return normalize_ohlcv(
        raw,
        symbol=symbol,
        timeframe=Timeframe.D1,
        provider=provider,
        fetched_at=now,
        now=now,
        origin=DataOrigin.FIXTURE,
    )


def trend_pullback_frame(symbol: str = "TRND") -> OHLCVFrame:
    """Uptrend halus (0,15%/hari), koreksi 3 hari ke area EMA20, lalu 1 hari naik (RSI 40–55)."""
    n = 300
    base = 1000.0 * (1.0015 ** np.arange(n))
    wobble = 1 + 0.004 * np.sin(np.arange(n) / 3.0)
    close = base * wobble
    level = close[-5]
    for k in range(3):
        level *= 0.992
        close[-4 + k] = level
    close[-1] = level * 1.006
    open_ = np.concatenate([[close[0]], close[:-1]])
    low = np.minimum(open_, close) - 5
    low[-2] = close[-2] * 0.996  # low pullback menyentuh area EMA20
    high = np.maximum(open_, close) + 5
    return _frame_from_close(close, symbol=symbol, open_=open_, high=high, low=low)


def breakout_frame(symbol: str = "BRKO", *, signal_high: float | None = None) -> OHLCVFrame:
    """Range 950–1050 selama 299 bar (EMA50 ≈ 1000), candle terakhir close 1080 volume 3×."""
    n = 300
    close = 1000 + 45 * np.sin(np.arange(n) / 4.0)
    close[-1] = 1080.0
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 5
    low = np.minimum(open_, close) - 5
    if signal_high is not None:
        high[-1] = signal_high
    volume = np.full(n, 8_000_000.0)
    volume[-1] = 24_000_000.0
    return _frame_from_close(close, symbol=symbol, open_=open_, high=high, low=low, volume=volume)


def reversal_frame(symbol: str = "RVSL") -> OHLCVFrame:
    """Sideways → 8 hari jatuh tajam (P1, RSI oversold) → pantulan → turun perlahan ke lower
    low (P2, RSI lebih tinggi) → 2 bar konfirmasi bullish."""
    n = 300
    close = 1000 + 8 * np.sin(np.arange(n) / 5.0)
    i = n - 30
    level = close[i - 1]
    for _ in range(8):  # jatuh tajam
        level *= 0.975
        close[i] = level
        i += 1
    p1 = level  # ≈ 817
    for _ in range(6):  # pantulan
        level *= 1.012
        close[i] = level
        i += 1
    for _ in range(14):  # turun perlahan ke lower low
        level *= 0.9915
        close[i] = level
        i += 1
    assert level < p1
    close[i] = level * 1.006  # bar konfirmasi 1
    close[i + 1] = close[i] * 1.010  # bar konfirmasi 2 (bar terakhir)
    assert i + 1 == n - 1
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 5
    low = np.minimum(open_, close) - 5
    return _frame_from_close(close, symbol=symbol, open_=open_, high=high, low=low)


def uptrend_frame(symbol: str = "UPTR") -> OHLCVFrame:
    """Uptrend halus tanpa koreksi: close > EMA20 pada bar terakhir (untuk uji foreign flow)."""
    n = 300
    close = 1000.0 * (1.0015 ** np.arange(n)) * (1 + 0.002 * np.sin(np.arange(n) / 3.0))
    return _frame_from_close(close, symbol=symbol)


def flat_frame(symbol: str = "FLAT") -> OHLCVFrame:
    n = 300
    close = 1000 + 3 * np.sin(np.arange(n) / 7.0)
    return _frame_from_close(close, symbol=symbol)


def illiquid_frame(symbol: str = "ILLQ") -> OHLCVFrame:
    n = 300
    close = 100 + np.zeros(n)
    volume = np.full(n, 10_000.0)
    return _frame_from_close(close, symbol=symbol, volume=volume)
