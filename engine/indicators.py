"""Indikator teknikal dengan warmup EKSPLISIT.

Pembungkus pandas-ta yang (a) membuang ``warmup`` bar pertama secara paksa karena NaN dari
library tidak dapat diandalkan sebagai penanda warmup (RSI hanya menghasilkan 1 NaN), dan
(b) memiliki test referensi terhadap rumus pandas-native agar penggantian library terdeteksi.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

with warnings.catch_warnings():
    # pandas-ta 0.4.71b0 mengatur opsi copy_on_write yang sudah usang di pandas 3.
    warnings.simplefilter("ignore")
    import pandas_ta as ta

EMA_FAST = 20
EMA_MID = 50
EMA_SLOW = 200
RSI_LENGTH = 14
ATR_LENGTH = 14
VOLUME_AVG_LENGTH = 20
RESISTANCE_LOOKBACK = 20
CMF_LENGTH = 20
MFI_LENGTH = 14

# Bar minimum agar semua indikator memiliki nilai yang sudah "matang".
WARMUP_BARS = EMA_SLOW + 50  # 250


class InsufficientHistoryError(ValueError):
    pass


def _series(values: pd.Series | None, index: pd.Index, name: str) -> pd.Series:
    if values is None:
        raise RuntimeError(f"pandas-ta mengembalikan None untuk {name}")
    out = pd.Series(np.asarray(values, dtype="float64"), index=index, name=name)
    return out


def ema(close: pd.Series, length: int) -> pd.Series:
    return _series(ta.ema(close, length=length), close.index, f"ema{length}")


def sma(close: pd.Series, length: int) -> pd.Series:
    return _series(ta.sma(close, length=length), close.index, f"sma{length}")


def rsi(close: pd.Series, length: int = RSI_LENGTH) -> pd.Series:
    return _series(ta.rsi(close, length=length), close.index, f"rsi{length}")


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = ATR_LENGTH) -> pd.Series:
    return _series(ta.atr(high, low, close, length=length), close.index, f"atr{length}")


def cmf(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, length: int = CMF_LENGTH
) -> pd.Series:
    """Chaikin Money Flow: Σ(MFM×vol)/Σvol; MFM = ((close−low) − (high−close)) / (high−low)."""
    return _series(ta.cmf(high, low, close, volume, length=length), close.index, f"cmf{length}")


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume (pandas-ta memberi NaN pada bar pertama; diisi 0 agar kumulatif konsisten)."""
    out = _series(ta.obv(close, volume), close.index, "obv")
    if len(out) and np.isnan(out.iloc[0]):
        out.iloc[0] = 0.0
    return out


def mfi(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series, length: int = MFI_LENGTH
) -> pd.Series:
    return _series(ta.mfi(high, low, close, volume, length=length), close.index, f"mfi{length}")


def apply_warmup(series: pd.Series, warmup: int) -> pd.Series:
    """Paksa ``warmup`` bar pertama menjadi NaN, apa pun yang dihasilkan library."""
    out = series.copy()
    if warmup > 0:
        out.iloc[: min(warmup, len(out))] = np.nan
    return out


def confirmed_pivot_lows(low: pd.Series, left: int, right: int) -> list[int]:
    """Indeks posisi pivot low yang SUDAH terkonfirmasi.

    Pivot pada posisi ``i`` hanya sah jika ``low[i]`` adalah minimum ketat terhadap ``left`` bar
    sebelumnya dan ``right`` bar sesudahnya; karena itu pivot baru diketahui pada bar ``i+right``
    dan tidak pernah memakai bar setelah bar terakhir yang tersedia.
    """
    values = low.to_numpy(dtype="float64")
    n = len(values)
    pivots: list[int] = []
    for i in range(left, n - right):
        window_left = values[i - left : i]
        window_right = values[i + 1 : i + 1 + right]
        if np.isnan(values[i]):
            continue
        if np.all(values[i] < window_left) and np.all(values[i] <= window_right):
            pivots.append(i)
    return pivots


def to_decimal(value: float | np.floating, places: int = 4) -> Decimal:
    if value is None or (isinstance(value, float | np.floating) and np.isnan(value)):
        raise ValueError("nilai NaN tidak dapat dikonversi ke Decimal")
    return Decimal(repr(round(float(value), places)))


@dataclass(frozen=True, slots=True)
class IndicatorSet:
    """Semua indikator untuk satu frame (kolom float64, warmup sudah dipaksakan)."""

    frame: (
        pd.DataFrame
    )  # open high low close volume + ema20 ema50 ema200 rsi14 atr14 vol_avg20 value
    warmup: int

    @property
    def usable(self) -> pd.DataFrame:
        return self.frame.iloc[self.warmup :]

    def last(self, column: str, offset: int = 0) -> float:
        value = self.frame[column].iloc[-1 - offset]
        if np.isnan(value):
            raise InsufficientHistoryError(f"{column} NaN pada offset {offset}")
        return float(value)


def compute_indicators(ohlcv: pd.DataFrame, *, warmup: int = WARMUP_BARS) -> IndicatorSet:
    """Hitung indikator dari frame OHLCV LENGKAP (bar belum lengkap harus dibuang pemanggil)."""
    required = ("open", "high", "low", "close", "volume")
    missing = [c for c in required if c not in ohlcv.columns]
    if missing:
        raise ValueError(f"kolom hilang: {missing}")
    if len(ohlcv) <= warmup:
        raise InsufficientHistoryError(
            f"histori {len(ohlcv)} bar <= warmup {warmup}; minimum {warmup + 1} bar lengkap"
        )
    df = ohlcv[list(required)].astype("float64").copy()
    close, high, low, volume = df["close"], df["high"], df["low"], df["volume"]
    df["ema20"] = apply_warmup(ema(close, EMA_FAST), warmup)
    df["ema50"] = apply_warmup(ema(close, EMA_MID), warmup)
    df["ema200"] = apply_warmup(ema(close, EMA_SLOW), warmup)
    df["rsi14"] = apply_warmup(rsi(close, RSI_LENGTH), warmup)
    df["atr14"] = apply_warmup(atr(high, low, close, ATR_LENGTH), warmup)
    df["vol_avg20"] = apply_warmup(volume.rolling(VOLUME_AVG_LENGTH).mean(), warmup)
    df["cmf20"] = apply_warmup(cmf(high, low, close, volume, CMF_LENGTH), warmup)
    df["obv"] = apply_warmup(obv(close, volume), warmup)
    df["obv_ema20"] = apply_warmup(ema(obv(close, volume).fillna(0.0), EMA_FAST), warmup)
    df["mfi14"] = apply_warmup(mfi(high, low, close, volume, MFI_LENGTH), warmup)
    # Nilai transaksi: kolom `value` bila provider memberi; jika tidak, aproksimasi close×volume.
    if "value" in ohlcv.columns and ohlcv["value"].notna().all():
        df["value"] = ohlcv["value"].astype("float64")
        df.attrs["value_basis"] = "provider"
    else:
        df["value"] = close * volume
        df.attrs["value_basis"] = "approx close×volume"
    df["value_avg20"] = apply_warmup(df["value"].rolling(VOLUME_AVG_LENGTH).mean(), warmup)
    return IndicatorSet(frame=df, warmup=warmup)
