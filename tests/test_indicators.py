"""Test referensi indikator terhadap rumus pandas-native + warmup eksplisit + pivot."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.indicators import (
    WARMUP_BARS,
    InsufficientHistoryError,
    apply_warmup,
    atr,
    compute_indicators,
    confirmed_pivot_lows,
    ema,
    rsi,
    sma,
    to_decimal,
)
from tests.engine_fixtures import flat_frame, trend_pullback_frame


@pytest.fixture(scope="module")
def series() -> pd.DataFrame:
    rng = np.random.default_rng(0)
    close = pd.Series(1000 + rng.normal(0, 5, 400).cumsum())
    return pd.DataFrame({"close": close, "high": close + 5, "low": close - 5})


def test_ema_matches_sma_seeded_recursion(series) -> None:
    length = 20
    close = series["close"]
    seed = close.copy()
    seed.iloc[: length - 1] = np.nan
    seed.iloc[length - 1] = close.iloc[:length].mean()
    ref = seed.ewm(span=length, adjust=False, ignore_na=True).mean()
    got = ema(close, length)
    np.testing.assert_allclose(got.iloc[length:], ref.iloc[length:], rtol=0, atol=1e-8)


def test_sma_matches_rolling_mean(series) -> None:
    np.testing.assert_allclose(
        sma(series["close"], 20).iloc[20:], series["close"].rolling(20).mean().iloc[20:]
    )


def test_rsi_matches_wilder(series) -> None:
    close = series["close"]
    delta = close.diff()
    up, down = delta.clip(lower=0), -delta.clip(upper=0)
    au = up.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ad = down.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    ref = 100 - 100 / (1 + au / ad)
    np.testing.assert_allclose(rsi(close, 14).iloc[50:], ref.iloc[50:], atol=1e-8)


def test_atr_matches_wilder_rma_after_seeding(series) -> None:
    h, lo, c = series["high"], series["low"], series["close"]
    tr = pd.concat([h - lo, (h - c.shift()).abs(), (lo - c.shift()).abs()], axis=1).max(axis=1)
    ref = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    got = atr(h, lo, c, 14)
    # seeding awal berbeda; setelah ~10 periode selisih meluruh di bawah 1e-2 pada skala ATR≈10
    np.testing.assert_allclose(got.iloc[200:], ref.iloc[200:], atol=1e-2)


def test_library_rsi_has_too_few_nans_so_warmup_is_forced(series) -> None:
    raw = rsi(series["close"], 14)
    assert raw.isna().sum() <= 1  # temuan Tahap 1: NaN library tidak bisa diandalkan
    forced = apply_warmup(raw, 250)
    assert forced.iloc[:250].isna().all() and forced.iloc[250:].notna().all()


def test_compute_indicators_enforces_warmup_and_columns() -> None:
    frame = trend_pullback_frame().complete_only().frame
    ind = compute_indicators(frame)
    assert ind.warmup == WARMUP_BARS
    for col in ("ema20", "ema50", "ema200", "rsi14", "atr14", "vol_avg20", "value_avg20"):
        assert ind.frame[col].iloc[:WARMUP_BARS].isna().all()
        assert ind.frame[col].iloc[WARMUP_BARS:].notna().all()
    assert len(ind.usable) == len(frame) - WARMUP_BARS
    assert ind.frame.attrs["value_basis"] == "approx close×volume"
    assert ind.last("close") == float(frame["close"].iloc[-1])


def test_compute_indicators_rejects_short_history() -> None:
    frame = flat_frame().complete_only().frame.iloc[:WARMUP_BARS]
    with pytest.raises(InsufficientHistoryError, match="minimum"):
        compute_indicators(frame)


def test_indicator_last_raises_on_nan() -> None:
    frame = flat_frame().complete_only().frame
    ind = compute_indicators(frame, warmup=len(frame) - 1)
    with pytest.raises(InsufficientHistoryError):
        ind.last("ema20", offset=1)


def test_confirmed_pivot_lows_need_right_bars() -> None:
    low = pd.Series([10, 9, 8, 7, 8, 9, 10, 9, 8, 6, 7, 8])
    #                0  1  2  3  4  5  6   7  8  9 10 11
    assert confirmed_pivot_lows(low, left=3, right=2) == [3, 9]
    # Tanpa 2 bar setelah indeks 9, pivot itu BELUM sah → tidak memakai masa depan.
    assert confirmed_pivot_lows(low.iloc[:10], left=3, right=2) == [3]
    assert confirmed_pivot_lows(low.iloc[:11], left=3, right=2) == [3]
    assert confirmed_pivot_lows(low.iloc[:12], left=3, right=2) == [3, 9]


def test_pivot_requires_strict_left_minimum() -> None:
    low = pd.Series([5, 5, 5, 5, 5, 5, 5, 5])
    assert confirmed_pivot_lows(low, left=2, right=2) == []


def test_to_decimal() -> None:
    assert str(to_decimal(1234.56789)) == "1234.5679"
    assert str(to_decimal(np.float64(2.5), 2)) == "2.5"
    with pytest.raises(ValueError):
        to_decimal(float("nan"))
