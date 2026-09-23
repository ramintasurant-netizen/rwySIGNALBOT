from __future__ import annotations

from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from data.providers.base import QualityStatus
from engine.pipeline import SignalEngine, SymbolInput
from engine.regime import Regime, RegimeConfig, compute_regime
from tests.engine_fixtures import (
    END_SESSION,
    _frame_from_close,
    breakout_frame,
    trend_pullback_frame,
)


def index_frame(kind: str, symbol: str = "^JKSE"):
    n = 320
    if kind == "bull":
        close = 6000.0 * (1.0012 ** np.arange(n))
    elif kind == "bear":
        close = 9000.0 * (0.9988 ** np.arange(n))
    else:  # sideways: EMA50 ≈ EMA200, close berosilasi
        close = 7000 + 40 * np.sin(np.arange(n) / 9.0)
    frame = _frame_from_close(close, symbol="IDXX")
    return replace(frame, symbol=symbol)


def test_regime_classification() -> None:
    cfg = RegimeConfig()
    bull = compute_regime(index_frame("bull"), END_SESSION, cfg)
    bear = compute_regime(index_frame("bear"), END_SESSION, cfg)
    assert bull.regime is Regime.BULLISH and bull.allow_new_longs(cfg)
    assert bear.regime is Regime.BEARISH and not bear.allow_new_longs(cfg)
    assert bull.close is not None and bull.ema_fast is not None and bull.ema_slow is not None
    assert "bullish" in bull.summary and "^JKSE" in bull.summary
    side = compute_regime(index_frame("side"), END_SESSION, cfg)
    assert side.regime in (Regime.NEUTRAL, Regime.BULLISH, Regime.BEARISH)
    if side.regime is Regime.NEUTRAL:
        assert side.allow_new_longs(cfg) and not side.allow_new_longs(
            RegimeConfig(block_neutral=True)
        )


def test_regime_unknown_cases_and_policy() -> None:
    cfg = RegimeConfig()
    none = compute_regime(None, END_SESSION, cfg)
    assert none.regime is Regime.UNKNOWN and not none.allow_new_longs(cfg)
    assert none.allow_new_longs(RegimeConfig(policy_on_unknown="allow"))
    stale = compute_regime(
        index_frame("bull"), date(2026, 3, 16), cfg
    )  # bar untuk 16 Mar tidak ada
    assert stale.regime is Regime.UNKNOWN and "tidak ada" in stale.reason
    short = index_frame("bull")
    short = replace(short, frame=short.frame.iloc[-100:])
    assert compute_regime(short, END_SESSION, cfg).regime is Regime.UNKNOWN
    off = RegimeConfig(enabled=False)
    assert compute_regime(None, END_SESSION, off).allow_new_longs(off)


def test_regime_no_lookahead() -> None:
    """Bar indeks setelah sesi evaluasi tidak boleh mengubah klasifikasi."""
    cfg = RegimeConfig()
    frame = index_frame("bull")
    base = compute_regime(frame, END_SESSION, cfg)
    df = frame.frame.copy()
    future = df.iloc[[-1]].copy()
    future.index = future.index + np.timedelta64(3, "D")
    future["session_date"] = date(2026, 3, 16)
    future["close"] = 1.0  # crash palsu di masa depan
    import pandas as pd

    extended = replace(frame, frame=pd.concat([df, future]))
    assert compute_regime(extended, END_SESSION, cfg) == base


def test_engine_holds_new_setups_in_bear_regime(market_rules) -> None:
    engine = SignalEngine(market_rules)
    universe = {
        "TRND": SymbolInput(trend_pullback_frame(), QualityStatus.DEGRADED),
        "BRKO": SymbolInput(breakout_frame(), QualityStatus.DEGRADED),
    }
    bull = engine.run(
        END_SESSION,
        universe,
        regime=compute_regime(index_frame("bull"), END_SESSION, engine.regime),
    )
    bear = engine.run(
        END_SESSION,
        universe,
        regime=compute_regime(index_frame("bear"), END_SESSION, engine.regime),
    )
    unknown = engine.run(
        END_SESSION, universe, regime=compute_regime(None, END_SESSION, engine.regime)
    )
    assert {c.symbol for c in bull.cards} == {"TRND", "BRKO"} and bull.regime == "bullish"
    assert bear.cards == () and bear.regime == "bearish"
    assert any("ditahan" in n for n in bear.notes) and not any(
        "tidak ada setup layak" in n for n in bear.notes
    )
    assert len(bear.evaluations) == 2 and all(
        e.card is not None for e in bear.evaluations
    )  # evaluasi tetap ada
    assert unknown.cards == () and unknown.regime == "unknown"  # default fail-closed
    allow = SignalEngine(market_rules, regime=RegimeConfig(policy_on_unknown="allow"))
    assert allow.run(
        END_SESSION, universe, regime=compute_regime(None, END_SESSION, allow.regime)
    ).cards
    off = SignalEngine(market_rules, regime=RegimeConfig(enabled=False))
    assert off.run(END_SESSION, universe).cards and off.run(END_SESSION, universe).regime is None
    assert engine.run(
        END_SESSION, universe
    ).cards  # tanpa regime diberikan = tidak difilter (pemanggil bertanggung jawab)


def test_regime_config_hash_and_validation(market_rules) -> None:
    a = SignalEngine(market_rules).config_hash
    b = SignalEngine(market_rules, regime=RegimeConfig(fast=20, slow=100)).config_hash
    assert a != b
    with pytest.raises(ValueError):
        RegimeConfig(fast=200, slow=50)


def test_regime_from_settings(settings_factory) -> None:
    from pydantic import ValidationError

    cfg = RegimeConfig.from_settings(
        settings_factory(regime_fast=20, regime_slow=100, regime_policy_on_unknown="allow")
    )
    assert (cfg.fast, cfg.slow, cfg.policy_on_unknown, cfg.index_symbol) == (
        20,
        100,
        "allow",
        "^JKSE",
    )
    with pytest.raises(ValidationError):
        settings_factory(regime_fast=200, regime_slow=100)
    with pytest.raises(ValidationError):
        settings_factory(regime_index_symbol="BBCA")


def test_backtest_runner_applies_regime_per_session(market_rules) -> None:
    from decimal import Decimal

    from backtest.runner import BacktestConfig, BacktestRunner

    frames = {"BRKO": breakout_frame("BRKO"), "TRND": trend_pullback_frame("TRND")}
    session = frames["BRKO"].frame["session_date"].iloc[-1]
    cfg = BacktestConfig(
        start=session,
        end=session,
        initial_capital=Decimal("100000000"),
        slippage_ticks=0,
        fee_buy_pct=Decimal("0"),
        fee_sell_pct=Decimal("0"),
    )
    engine = SignalEngine(market_rules)
    bull = BacktestRunner(engine, market_rules, cfg).run(frames, index_frame=index_frame("bull"))
    bear = BacktestRunner(engine, market_rules, cfg).run(frames, index_frame=index_frame("bear"))
    none = BacktestRunner(engine, market_rules, cfg).run(frames)
    assert bull.metrics.signals_generated == 2 and any("bullish=1" in n for n in bull.notes)
    assert bear.metrics.signals_generated == 0 and any("bearish=1" in n for n in bear.notes)
    assert none.metrics.signals_generated == 0 and any(
        "data indeks tidak diberikan" in n for n in none.notes
    )
