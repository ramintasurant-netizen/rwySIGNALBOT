from __future__ import annotations

from decimal import Decimal

from backtest.research import (
    DEFAULT_VARIANTS,
    Variant,
    format_table,
    load_cached,
    run_variants,
    save_frame,
)
from backtest.runner import BacktestConfig
from engine.regime import RegimeConfig
from engine.risk import RiskConfig
from tests.engine_fixtures import breakout_frame, trend_pullback_frame
from tests.test_regime import index_frame


def test_cache_roundtrip_preserves_frames(tmp_path) -> None:
    frame = breakout_frame("BRKO")
    idx = index_frame("bull")
    save_frame(frame, tmp_path)
    save_frame(idx, tmp_path)
    frames, index = load_cached(tmp_path)
    assert set(frames) == {"BRKO"} and index is not None and index.symbol == "^JKSE"
    back = frames["BRKO"]
    assert len(back) == len(frame) and str(back.frame.index.tz) == "UTC"
    assert list(back.frame["session_date"]) == list(frame.frame["session_date"])
    assert back.frame["close"].tolist() == frame.frame["close"].tolist()
    assert back.frame["complete"].dtype == bool and back.origin == frame.origin


def test_variants_are_distinct_and_run_on_in_sample(market_rules) -> None:
    names = [v.name for v in DEFAULT_VARIANTS]
    assert len(names) == len(set(names)) and "base" in names
    frames = {"BRKO": breakout_frame("BRKO"), "TRND": trend_pullback_frame("TRND")}
    session = frames["BRKO"].frame["session_date"].iloc[-1]
    cfg = BacktestConfig(
        start=session,
        end=session,
        slippage_ticks=0,
        fee_buy_pct=Decimal("0"),
        fee_sell_pct=Decimal("0"),
    )
    variants = (
        Variant("v_bull", "base", regime_enabled=True),
        Variant("v_nobrk", "tanpa breakout", weights={"breakout": Decimal("0")}),
        Variant("v_off", "tanpa rezim", regime_enabled=False),
    )
    rows = run_variants(
        variants, frames, index_frame("bull"), rules=market_rules, risk=RiskConfig(), cfg=cfg
    )
    by = {r.variant.name: r for r in rows}
    assert by["v_bull"].result.metrics.signals_generated == 2
    assert (
        by["v_nobrk"].result.metrics.signals_generated == 1
    )  # breakout bobot 0 → di bawah threshold
    assert by["v_off"].result.metrics.signals_generated == 2
    assert len({r.result.config_hash for r in rows}) == 3  # setiap varian = konfigurasi berbeda
    table = format_table(rows)
    assert "v_bull" in table and "trades" in table
    # RegimeConfig dari varian sesuai
    assert variants[2].engine(market_rules, RiskConfig()).regime == RegimeConfig(enabled=False)
