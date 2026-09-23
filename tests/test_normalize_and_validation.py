from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from core.timeutil import WIB
from data.providers.base import (
    DataOrigin,
    PriceBasis,
    QualityStatus,
    Quote,
    Timeframe,
    normalize_ohlcv,
)
from data.validation import cross_validate_close, validate_ohlcv, validate_quote
from tests.conftest import make_frame, make_yf_frame

NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)  # Senin 08:00 WIB


def test_normalize_naive_index_is_treated_as_wib_and_stored_utc() -> None:
    raw = make_yf_frame(5, tz=None)
    frame = normalize_ohlcv(
        raw, symbol="BBCA", timeframe=Timeframe.D1, provider="t", fetched_at=NOW, now=NOW
    )
    assert str(frame.frame.index.tz) == "UTC"
    # 00:00 WIB == 17:00 UTC hari sebelumnya
    assert frame.frame.index[-1] == pd.Timestamp("2026-03-12 17:00", tz="UTC")
    assert frame.frame["session_date"].iloc[-1] == date(2026, 3, 13)
    assert frame.frame["complete"].all()
    assert frame.price_basis is PriceBasis.RAW


def test_normalize_sorts_and_flags_duplicates() -> None:
    raw = make_yf_frame(5)
    shuffled = pd.concat([raw.iloc[[3, 1, 4, 0, 2]], raw.iloc[[2]]])
    frame = normalize_ohlcv(
        shuffled, symbol="BBCA", timeframe=Timeframe.D1, provider="t", fetched_at=NOW, now=NOW
    )
    assert frame.frame.index.is_monotonic_increasing
    assert any("duplikat" in n for n in frame.notes)
    report = validate_ohlcv(frame, min_bars=1)
    assert report.status is QualityStatus.INVALID
    assert any("duplikat" in i for i in report.issues)


def test_adjusted_series_is_separate_object_and_raw_untouched() -> None:
    raw = make_yf_frame(10)
    f_raw = normalize_ohlcv(
        raw, symbol="BBCA", timeframe=Timeframe.D1, provider="t", fetched_at=NOW, now=NOW
    )
    f_adj = normalize_ohlcv(
        raw,
        symbol="BBCA",
        timeframe=Timeframe.D1,
        provider="t",
        fetched_at=NOW,
        now=NOW,
        price_basis=PriceBasis.ADJUSTED,
    )
    assert f_raw.price_basis is PriceBasis.RAW and f_adj.price_basis is PriceBasis.ADJUSTED
    np.testing.assert_allclose(
        f_adj.frame["close"].to_numpy(), f_raw.frame["close"].to_numpy() * 0.98
    )
    np.testing.assert_allclose(f_raw.frame["close"].to_numpy(), raw["Close"].to_numpy())
    assert f_raw.frame is not f_adj.frame


def test_adjusted_requires_adj_close_column() -> None:
    raw = make_yf_frame(10, with_adj_close=False)
    with pytest.raises(ValueError, match="adj_close"):
        normalize_ohlcv(
            raw,
            symbol="BBCA",
            timeframe=Timeframe.D1,
            provider="t",
            fetched_at=NOW,
            now=NOW,
            price_basis=PriceBasis.ADJUSTED,
        )


def test_incomplete_daily_bar_detected_via_rules(market_rules) -> None:
    # Bar hari ini (Senin 16 Mar) diambil 10:00 WIB => belum lengkap.
    fetched = datetime(2026, 3, 16, 3, 0, tzinfo=UTC)
    raw = make_yf_frame(5, end_session=date(2026, 3, 16))
    frame = normalize_ohlcv(
        raw,
        symbol="BBCA",
        timeframe=Timeframe.D1,
        provider="t",
        fetched_at=fetched,
        now=fetched,
        is_daily_complete=market_rules.is_daily_bar_complete,
    )
    assert frame.frame["complete"].tolist() == [True, True, True, True, False]
    assert frame.last_complete_session == date(2026, 3, 13)
    assert len(frame.complete_only()) == 4


def test_intraday_completeness_uses_bar_end() -> None:
    now = datetime(2026, 3, 16, 9, 4, 30, tzinfo=WIB)
    raw = make_yf_frame(6, end_session=date(2026, 3, 16), intraday=True)  # 09:00..09:05 WIB
    frame = normalize_ohlcv(
        raw, symbol="BBCA", timeframe=Timeframe.M1, provider="t", fetched_at=now, now=now
    )
    # bar 09:00..09:03 lengkap (berakhir <= 09:04:30); 09:04 dan 09:05 belum
    assert frame.frame["complete"].tolist() == [True, True, True, True, False, False]


def test_corporate_actions_extracted() -> None:
    raw = make_yf_frame(5)
    raw.loc[raw.index[2], "Dividends"] = 25.0
    raw.loc[raw.index[3], "Stock Splits"] = 2.0
    frame = normalize_ohlcv(
        raw, symbol="BBCA", timeframe=Timeframe.D1, provider="t", fetched_at=NOW, now=NOW
    )
    kinds = {(a.kind, a.value) for a in frame.corporate_actions}
    assert kinds == {("dividend", Decimal("25.0")), ("split", Decimal("2.0"))}


def test_missing_columns_rejected() -> None:
    raw = make_yf_frame(5).drop(columns=["Volume"])
    with pytest.raises(ValueError, match="hilang"):
        normalize_ohlcv(
            raw, symbol="BBCA", timeframe=Timeframe.D1, provider="t", fetched_at=NOW, now=NOW
        )


# ----------------------------------------------------------------------------- validasi


def test_valid_frame_is_ok() -> None:
    frame = make_frame(n=300, now=NOW)
    report = validate_ohlcv(frame, min_bars=250, expected_last_session=date(2026, 3, 13))
    assert report.status is QualityStatus.OK
    assert report.bars_complete == 300 and report.last_complete_session == date(2026, 3, 13)


def test_insufficient_history_is_missing() -> None:
    frame = make_frame(n=100, now=NOW)
    report = validate_ohlcv(frame, min_bars=250)
    assert report.status is QualityStatus.MISSING
    assert "100 bar lengkap < minimum 250" in report.issues[0]


def test_stale_daily_when_expected_session_newer() -> None:
    frame = make_frame(n=300, end_session=date(2026, 3, 12), now=NOW)
    report = validate_ohlcv(frame, min_bars=250, expected_last_session=date(2026, 3, 13))
    assert report.status is QualityStatus.STALE


def test_broken_ohlc_relations_are_invalid() -> None:
    raw = make_yf_frame(20)
    raw.loc[raw.index[5], "Low"] = raw.loc[raw.index[5], "Close"] + 100  # low > close
    raw.loc[raw.index[6], "Volume"] = -1
    raw.loc[raw.index[7], "Open"] = np.nan
    frame = normalize_ohlcv(
        raw, symbol="BBCA", timeframe=Timeframe.D1, provider="t", fetched_at=NOW, now=NOW
    )
    report = validate_ohlcv(frame, min_bars=1)
    assert report.status is QualityStatus.INVALID
    joined = " | ".join(report.issues)
    assert "melanggar" in joined and "volume negatif" in joined and "OHLC kosong" in joined


def test_intraday_freshness() -> None:
    now = datetime(2026, 3, 16, 9, 30, tzinfo=WIB)
    frame = make_frame(
        timeframe=Timeframe.M1, n=10, end_session=date(2026, 3, 16), now=now
    )  # 09:00..09:09
    fresh = validate_ohlcv(frame, min_bars=1, now=now, max_age=timedelta(minutes=30))
    stale = validate_ohlcv(frame, min_bars=1, now=now, max_age=timedelta(minutes=15))
    assert fresh.status is QualityStatus.OK
    assert stale.status is QualityStatus.STALE
    assert validate_ohlcv(frame, min_bars=1).status is QualityStatus.INVALID  # tanpa now/max_age


def test_quote_validation() -> None:
    q = Quote(
        "BBCA", Decimal("9450"), NOW - timedelta(minutes=5), NOW, "t", origin=DataOrigin.FIXTURE
    )
    assert validate_quote(q, now=NOW, max_age=timedelta(minutes=20)).status is QualityStatus.OK
    assert validate_quote(q, now=NOW, max_age=timedelta(minutes=2)).status is QualityStatus.STALE
    bad = Quote("BBCA", Decimal("0"), NOW, NOW, "t")
    assert (
        validate_quote(bad, now=NOW, max_age=timedelta(minutes=20)).status is QualityStatus.INVALID
    )


# ----------------------------------------------------------------------------- cross-validation


def test_cross_validation_ok_and_suspect() -> None:
    a = make_frame(provider="a", now=NOW)
    b_same = make_frame(provider="b", now=NOW)
    ok = cross_validate_close(a, b_same, max_diff_pct=Decimal("0.5"))
    assert ok.passed and ok.diff_pct == Decimal("0")
    b_other = make_frame(provider="b", now=NOW, seed=7)
    suspect = cross_validate_close(a, b_other, max_diff_pct=Decimal("0.5"))
    assert suspect.status == "suspect" and suspect.session_date == date(2026, 3, 13)


def test_cross_validation_not_comparable_cases() -> None:
    a = make_frame(provider="a", now=NOW)
    adj = make_frame(provider="b", now=NOW, price_basis=PriceBasis.ADJUSTED)
    assert cross_validate_close(a, adj, max_diff_pct=Decimal("0.5")).status == "not_comparable"
    older = make_frame(provider="b", now=NOW, end_session=date(2026, 3, 12))
    res = cross_validate_close(a, older, max_diff_pct=Decimal("0.5"))
    assert res.status == "not_comparable" and "tidak ada di kedua provider" in res.reason
    other_symbol = make_frame(symbol="BBRI", provider="b", now=NOW)
    assert (
        cross_validate_close(a, other_symbol, max_diff_pct=Decimal("0.5")).status
        == "not_comparable"
    )
