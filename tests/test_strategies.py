from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from data.providers.base import BrokerEntry, BrokerSummary, DataOrigin, ForeignFlow, QualityStatus
from engine.indicators import compute_indicators
from engine.models import StrategyState
from engine.strategies import (
    BreakoutStrategy,
    ForeignFlowStrategy,
    ReversalStrategy,
    TrendPullbackStrategy,
)
from engine.strategies.base import StrategyContext
from tests.engine_fixtures import (
    END_SESSION,
    NOW,
    _frame_from_close,
    breakout_frame,
    flat_frame,
    reversal_frame,
    trend_pullback_frame,
    uptrend_frame,
)


def _ctx(frame, **kwargs) -> StrategyContext:
    ind = compute_indicators(frame.complete_only().frame)
    return StrategyContext(frame.symbol, END_SESSION, ind, QualityStatus.DEGRADED, **kwargs)


def _mutate_last(frame, **cols):
    df = frame.frame.copy()
    for col, value in cols.items():
        df.loc[df.index[-1], col] = value
    return frame.__class__(**{**{f: getattr(frame, f) for f in frame.__slots__}, "frame": df})


# ----------------------------------------------------------------------------- matriks


@pytest.mark.parametrize(
    ("frame_factory", "expected"),
    [
        (trend_pullback_frame, "trend_pullback"),
        (breakout_frame, "breakout"),
        (reversal_frame, "reversal"),
        (flat_frame, None),
    ],
)
def test_each_fixture_triggers_only_its_strategy(frame_factory, expected) -> None:
    ctx = _ctx(frame_factory())
    fired = {
        s.id
        for s in (TrendPullbackStrategy(), BreakoutStrategy(), ReversalStrategy())
        if s.outcome(ctx).state is StrategyState.SIGNAL
    }
    assert fired == ({expected} if expected else set())


def test_outcomes_are_deterministic() -> None:
    ctx = _ctx(trend_pullback_frame())
    a = TrendPullbackStrategy().outcome(ctx)
    b = TrendPullbackStrategy().outcome(ctx)
    assert a == b and a.signal is not None
    assert a.signal.strategy_version == "1.0.0"
    assert set(a.signal.evidence) >= {"close", "ema20", "ema50", "ema200", "rsi14", "atr14"}
    assert all(isinstance(v, Decimal) for k, v in a.signal.evidence.items())


# ----------------------------------------------------------------------------- trend pullback


def test_trend_pullback_conditions_are_individually_necessary() -> None:
    base = trend_pullback_frame()
    strat = TrendPullbackStrategy()
    assert strat.outcome(_ctx(base)).state is StrategyState.SIGNAL
    ema50 = float(compute_indicators(base.complete_only().frame).frame["ema50"].iloc[-1])
    below_trend = _mutate_last(base, close=ema50 * 0.99, low=ema50 * 0.98)
    assert strat.outcome(_ctx(below_trend)).state is StrategyState.NO_SETUP
    prev_close = float(base.frame["close"].iloc[-2])
    no_confirmation = _mutate_last(base, close=prev_close * 0.999)
    assert strat.outcome(_ctx(no_confirmation)).state is StrategyState.NO_SETUP
    assert (
        TrendPullbackStrategy(rsi_low=56, rsi_high=70).outcome(_ctx(base)).state
        is StrategyState.NO_SETUP
    )


def test_trend_pullback_hints_are_ordered() -> None:
    sig = TrendPullbackStrategy().outcome(_ctx(trend_pullback_frame())).signal
    assert sig is not None
    assert sig.entry_low_hint <= sig.entry_high_hint
    assert sig.structure_stop_hint is not None and sig.structure_stop_hint < sig.entry_low_hint


# ----------------------------------------------------------------------------- breakout


def test_breakout_resistance_excludes_signal_candle() -> None:
    # High candle sinyal 1200 jauh di atas close 1080. Jika candle sinyal ikut membentuk
    # resistance, close < resistance dan sinyal hilang. Spesifikasi: harus TETAP sinyal.
    with_tall_wick = breakout_frame(signal_high=1200.0)
    out = BreakoutStrategy().outcome(_ctx(with_tall_wick))
    assert out.state is StrategyState.SIGNAL
    assert out.signal is not None and out.signal.evidence["resistance"] < Decimal("1080")


def test_breakout_requires_volume_and_close_above_resistance() -> None:
    base = breakout_frame()
    strat = BreakoutStrategy()
    low_volume = _mutate_last(base, volume=10_000_000.0)  # 1.25× < 1.5×
    assert strat.outcome(_ctx(low_volume)).state is StrategyState.NO_SETUP
    resistance = float(strat.outcome(_ctx(base)).signal.evidence["resistance"])
    weak_close = _mutate_last(base, close=resistance - 5)
    assert strat.outcome(_ctx(weak_close)).state is StrategyState.NO_SETUP
    assert BreakoutStrategy(volume_multiple=4.0).outcome(_ctx(base)).state is StrategyState.NO_SETUP


def test_breakout_volume_average_excludes_signal_candle() -> None:
    sig = BreakoutStrategy().outcome(_ctx(breakout_frame())).signal
    assert sig is not None
    assert sig.evidence["volume_avg_prior"] == Decimal("8000000")  # tanpa 24 juta candle sinyal
    assert sig.evidence["volume_ratio"] == Decimal("3.0")


# ----------------------------------------------------------------------------- reversal


def test_reversal_requires_confirmed_pivot_and_confirmation_candle() -> None:
    base = reversal_frame()
    strat = ReversalStrategy()
    out = strat.outcome(_ctx(base))
    assert out.state is StrategyState.SIGNAL and out.signal is not None
    ev = out.signal.evidence
    assert ev["pivot2_low"] < ev["pivot1_low"] and ev["pivot2_rsi"] > ev["pivot1_rsi"]
    assert ev["rsi_min"] <= Decimal("30")
    assert ev["bars_since_confirmation"] == 0

    # Bar konfirmasi bearish → tidak ada sinyal.
    open_last = float(base.frame["open"].iloc[-1])
    bearish = _mutate_last(base, close=open_last * 0.995)
    assert strat.outcome(_ctx(bearish)).state is StrategyState.NO_SETUP

    # Tanpa bar konfirmasi terakhir, pivot P2 belum sah (butuh pivot_right bar) → tidak ada sinyal.
    truncated = base.__class__(
        **{**{f: getattr(base, f) for f in base.__slots__}, "frame": base.frame.iloc[:-1]}
    )
    ind = compute_indicators(truncated.complete_only().frame)
    ctx = StrategyContext(
        "RVSL", truncated.frame["session_date"].iloc[-1], ind, QualityStatus.DEGRADED
    )
    assert strat.outcome(ctx).state is StrategyState.NO_SETUP


def test_reversal_no_lookahead_signal_only_after_confirmation_bar() -> None:
    """Menambahkan bar masa depan tidak boleh mengubah keputusan pada bar evaluasi."""
    base = reversal_frame()
    strat = ReversalStrategy()
    decision_at_t = strat.outcome(_ctx(base))
    df = base.frame
    future = df.iloc[[-1]].copy()
    future.index = future.index + np.timedelta64(3, "D")
    future["close"] = future["close"] * 1.2
    future["high"] = future["high"] * 1.2
    future["session_date"] = END_SESSION.replace(day=16)
    extended = base.__class__(
        **{**{f: getattr(base, f) for f in base.__slots__}, "frame": pd.concat([df, future])}
    )
    # Pipeline memotong ke session_date; di sini kita meniru pemotongan itu.
    sliced = extended.frame.loc[extended.frame["session_date"] <= END_SESSION]
    ind = compute_indicators(sliced)
    again = strat.outcome(StrategyContext("RVSL", END_SESSION, ind, QualityStatus.DEGRADED))
    assert again == decision_at_t


# ----------------------------------------------------------------------------- foreign flow


def _flows(values: list[str], frame) -> tuple[ForeignFlow, ...]:
    sessions = list(frame.frame["session_date"].iloc[-len(values) :])
    return tuple(
        ForeignFlow(frame.symbol, s, Decimal(v), "fixture", NOW, origin=DataOrigin.FIXTURE)
        for s, v in zip(sessions, values, strict=True)
    )


def test_foreign_flow_inactive_without_data() -> None:
    out = ForeignFlowStrategy().outcome(
        _ctx(trend_pullback_frame(), foreign_flow_reason="provider tidak mendukung")
    )
    assert out.state is StrategyState.INACTIVE and "tidak mendukung" in out.reason


def test_foreign_flow_signal_and_legit_zero() -> None:
    frame = uptrend_frame()
    strat = ForeignFlowStrategy()
    ok = strat.outcome(
        _ctx(frame, foreign_flows=_flows(["5000000000", "7000000000", "6000000000"], frame))
    )
    assert ok.state is StrategyState.SIGNAL and ok.signal is not None
    assert ok.signal.data_requirements == ("ohlcv_daily_complete", "foreign_flow")
    # Nol yang sah pada salah satu sesi = bukan net buy beruntun → NO_SETUP (bukan INACTIVE).
    zero = strat.outcome(
        _ctx(frame, foreign_flows=_flows(["5000000000", "0", "6000000000"], frame))
    )
    assert zero.state is StrategyState.NO_SETUP
    # Sesi terakhir tidak sama dengan sesi evaluasi → INACTIVE (data tidak segar).
    stale_sessions = _flows(["1", "1", "1"], frame)
    shifted = tuple(
        ForeignFlow(
            f.symbol,
            f.session_date.replace(day=f.session_date.day - 1),
            f.net_value,
            f.provider,
            f.fetched_at,
        )
        for f in stale_sessions
    )
    assert strat.outcome(_ctx(frame, foreign_flows=shifted)).state is StrategyState.INACTIVE


def test_foreign_flow_broker_concentration_bonus() -> None:
    frame = uptrend_frame()
    flows = _flows(["5000000000", "7000000000", "6000000000"], frame)
    without = ForeignFlowStrategy().outcome(_ctx(frame, foreign_flows=flows)).signal
    summary = BrokerSummary(
        "UPTR",
        END_SESSION,
        (
            BrokerEntry("AA", Decimal("9000000000"), Decimal("1000000000")),
            BrokerEntry("BB", Decimal("2000000000"), Decimal("1000000000")),
            BrokerEntry("CC", Decimal("500000000"), Decimal("3000000000")),
        ),
        "fixture",
        NOW,
    )
    with_broker = (
        ForeignFlowStrategy()
        .outcome(_ctx(frame, foreign_flows=flows, broker_summary=summary))
        .signal
    )
    assert without is not None and with_broker is not None
    assert with_broker.score == min(100, without.score + 10)
    assert with_broker.evidence["dominant_broker"] == "AA"
    assert any("Akumulasi terkonsentrasi" in r for r in with_broker.reasons)


def test_strategy_insufficient_data_state() -> None:
    frame = flat_frame()
    ind = compute_indicators(frame.complete_only().frame, warmup=len(frame) - 3)
    ctx = StrategyContext("FLAT", END_SESSION, ind, QualityStatus.OK)
    out = BreakoutStrategy().outcome(ctx)
    assert out.state is StrategyState.INSUFFICIENT_DATA and "bar matang" in out.reason


def test_fixture_frames_are_labeled_synthetic() -> None:
    for f in (trend_pullback_frame(), breakout_frame(), reversal_frame(), flat_frame()):
        assert f.origin is DataOrigin.FIXTURE
        assert isinstance(_frame_from_close, object)
