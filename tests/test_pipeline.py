from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import numpy as np
import pandas as pd

from config.market_rules import MarketRules
from data.providers.base import DataOrigin, PriceBasis, QualityStatus
from engine import ENGINE_VERSION
from engine.models import StrategyState
from engine.pipeline import SignalEngine, SymbolInput
from engine.risk import RiskConfig
from engine.scorer import ScorerConfig
from engine.screener import Screener
from tests.engine_fixtures import (
    END_SESSION,
    breakout_frame,
    flat_frame,
    illiquid_frame,
    reversal_frame,
    trend_pullback_frame,
)


def _universe(quality: QualityStatus = QualityStatus.DEGRADED) -> dict[str, SymbolInput]:
    return {
        "TRND": SymbolInput(trend_pullback_frame(), quality, foreign_flow_reason="tidak didukung"),
        "BRKO": SymbolInput(breakout_frame(), quality, foreign_flow_reason="tidak didukung"),
        "RVSL": SymbolInput(reversal_frame(), quality, foreign_flow_reason="tidak didukung"),
        "FLAT": SymbolInput(flat_frame(), quality, foreign_flow_reason="tidak didukung"),
        "ILLQ": SymbolInput(illiquid_frame(), quality, foreign_flow_reason="tidak didukung"),
    }


def test_engine_run_end_to_end(market_rules: MarketRules) -> None:
    engine = SignalEngine(market_rules)
    result = engine.run(END_SESSION, _universe())
    assert result.engine_version == ENGINE_VERSION and len(result.config_hash) == 16
    assert result.universe == ("BRKO", "FLAT", "ILLQ", "RVSL", "TRND")
    by_symbol = {c.symbol: c for c in result.cards}
    assert set(by_symbol) == {"TRND", "BRKO", "RVSL"}
    assert by_symbol["TRND"].primary_strategy == "trend_pullback"
    assert by_symbol["BRKO"].primary_strategy == "breakout"
    assert by_symbol["RVSL"].primary_strategy == "reversal"
    for card in result.cards:
        assert card.confidence >= engine.scorer.threshold
        assert card.score_breakdown["penalty"] == 10  # kualitas degraded
        r = card.risk
        assert r.stop_loss < r.entry_low <= r.entry_high < r.tp1 <= r.tp2 <= r.tp3
        assert r.rr_tp1_net >= engine.risk.min_rr
        assert r.sizing is not None and r.sizing.lots >= 0
        assert card.data.session_date == END_SESSION and card.data.quality is QualityStatus.DEGRADED
        assert card.data.provider == "fixture"
        assert card.strategy_versions == engine.strategy_versions
        assert card.reasons and card.evidence
    blocked = {b.symbol: b for b in result.blocked}
    assert blocked["ILLQ"].stage == "screener" and "nilai transaksi" in blocked["ILLQ"].reason
    flat = next(e for e in result.evaluations if e.symbol == "FLAT")
    assert flat.card is None and flat.blocked is None and flat.confidence is None
    assert {o.state for o in flat.outcomes} <= {StrategyState.NO_SETUP, StrategyState.INACTIVE}
    # Urutan kartu deterministik: confidence ↓ lalu tie-break.
    assert [c.confidence for c in result.cards] == sorted(
        (c.confidence for c in result.cards), reverse=True
    )


def test_engine_is_deterministic(market_rules: MarketRules) -> None:
    a = SignalEngine(market_rules).run(END_SESSION, _universe())
    b = SignalEngine(market_rules).run(END_SESSION, _universe())
    assert a.config_hash == b.config_hash
    assert [(c.symbol, c.confidence, c.risk) for c in a.cards] == [
        (c.symbol, c.confidence, c.risk) for c in b.cards
    ]


def test_ok_quality_has_no_penalty(market_rules: MarketRules) -> None:
    result = SignalEngine(market_rules).run(END_SESSION, _universe(QualityStatus.OK))
    assert all(c.score_breakdown["penalty"] == 0 for c in result.cards)


def test_unusable_quality_blocks_symbol(market_rules: MarketRules) -> None:
    engine = SignalEngine(market_rules)
    for q in (
        QualityStatus.SUSPECT,
        QualityStatus.STALE,
        QualityStatus.MISSING,
        QualityStatus.INVALID,
    ):
        ev = engine.evaluate_symbol("TRND", SymbolInput(trend_pullback_frame(), q), END_SESSION)
        assert (
            ev.blocked is not None and ev.blocked.stage == "data" and q.value in ev.blocked.reason
        )


def test_adjusted_series_rejected(market_rules: MarketRules) -> None:
    frame = replace(trend_pullback_frame(), price_basis=PriceBasis.ADJUSTED)
    ev = SignalEngine(market_rules).evaluate_symbol(
        "TRND", SymbolInput(frame, QualityStatus.OK), END_SESSION
    )
    assert ev.blocked is not None and "raw" in ev.blocked.reason


def test_session_mismatch_and_lookahead_boundary(market_rules: MarketRules) -> None:
    engine = SignalEngine(market_rules)
    frame = trend_pullback_frame()
    # Sesi evaluasi lebih lama dari bar terakhir: engine memotong frame, lalu mendeteksi sesi
    # lengkap terakhir ≠ sesi evaluasi bila tanggal tidak ada; bila ada, memakai data s/d tanggal itu.
    earlier = frame.frame["session_date"].iloc[-2]
    ev = engine.evaluate_symbol("TRND", SymbolInput(frame, QualityStatus.OK), earlier)
    assert ev.blocked is None or ev.blocked.stage in {"risk"}
    # Tidak ada bar untuk tanggal Sabtu → sesi lengkap terakhir (Jumat) ≠ Sabtu → diblokir.
    saturday = END_SESSION + timedelta(days=1)
    ev2 = engine.evaluate_symbol("TRND", SymbolInput(frame, QualityStatus.OK), saturday)
    assert ev2.blocked is not None and "≠ sesi evaluasi" in ev2.blocked.reason
    # Bar masa depan tidak boleh memengaruhi keputusan pada END_SESSION.
    df = frame.frame
    future = df.iloc[[-1]].copy()
    future.index = future.index + np.timedelta64(3, "D")
    future["session_date"] = END_SESSION + timedelta(days=3)
    future["close"] = 1.0  # crash palsu di masa depan
    future["low"] = 0.5
    extended = replace(frame, frame=pd.concat([df, future]))
    base_ev = engine.evaluate_symbol("TRND", SymbolInput(frame, QualityStatus.OK), END_SESSION)
    ext_ev = engine.evaluate_symbol("TRND", SymbolInput(extended, QualityStatus.OK), END_SESSION)
    assert base_ev.outcomes == ext_ev.outcomes
    assert base_ev.card is not None and ext_ev.card is not None
    assert base_ev.card.risk == ext_ev.card.risk


def test_incomplete_last_bar_is_ignored(market_rules: MarketRules) -> None:
    frame = trend_pullback_frame()
    df = frame.frame.copy()
    df.loc[df.index[-1], "complete"] = False
    partial = replace(frame, frame=df)
    ev = SignalEngine(market_rules).evaluate_symbol(
        "TRND", SymbolInput(partial, QualityStatus.OK), END_SESSION
    )
    assert ev.blocked is not None and "sesi lengkap terakhir" in ev.blocked.reason


def test_insufficient_history_blocked(market_rules: MarketRules) -> None:
    frame = trend_pullback_frame()
    short = replace(frame, frame=frame.frame.iloc[-200:])
    ev = SignalEngine(market_rules).evaluate_symbol(
        "TRND", SymbolInput(short, QualityStatus.OK), END_SESSION
    )
    assert ev.blocked is not None and ev.blocked.stage == "indicators"


def test_exclusions_block_before_data(market_rules: MarketRules) -> None:
    data = market_rules.model_dump(mode="json")
    data["exclusions"] = {
        "suspended_symbols": ["TRND"],
        "special_monitoring_board_symbols": ["BRKO"],
    }
    rules = MarketRules.model_validate(data)
    result = SignalEngine(rules).run(END_SESSION, _universe())
    blocked = {b.symbol: b.reason for b in result.blocked}
    assert "suspensi" in blocked["TRND"] and "pemantauan khusus" in blocked["BRKO"]
    assert {c.symbol for c in result.cards} == {"RVSL"}


def test_threshold_and_max_signals(market_rules: MarketRules) -> None:
    strict = SignalEngine(market_rules, scorer=ScorerConfig(threshold=95))
    result = strict.run(END_SESSION, _universe(QualityStatus.OK))
    assert all(c.confidence >= 95 for c in result.cards)
    capped = SignalEngine(market_rules, scorer=ScorerConfig(max_signals=1))
    assert len(capped.run(END_SESSION, _universe()).cards) == 1


def test_risk_rejection_is_reported_not_forced(market_rules: MarketRules) -> None:
    # Zona entry maksimal hampir nol: setup dengan zona lebar ditolak di tahap risk (tidak
    # dipaksa menyempit); hanya setup yang zonanya memang satu harga yang lolos.
    engine = SignalEngine(market_rules, risk=RiskConfig(max_entry_zone_pct=Decimal("0.01")))
    result = engine.run(END_SESSION, _universe())
    stages = {b.symbol: (b.stage, b.reason) for b in result.blocked}
    assert stages["BRKO"][0] == "risk" and "lebih lebar" in stages["BRKO"][1]
    assert stages["RVSL"][0] == "risk"
    for card in result.cards:
        assert card.risk.entry_low == card.risk.entry_high
    brko = next(e for e in result.evaluations if e.symbol == "BRKO")
    assert brko.confidence is not None and brko.card is None  # skor ada, kartu tidak dipaksakan


def test_config_hash_changes_with_material_config(market_rules: MarketRules) -> None:
    base = SignalEngine(market_rules).config_hash
    assert SignalEngine(market_rules, risk=RiskConfig(min_rr=Decimal("2.5"))).config_hash != base
    assert SignalEngine(market_rules, scorer=ScorerConfig(threshold=75)).config_hash != base
    data = market_rules.model_dump(mode="json")
    data["lot_size"] = 50
    assert SignalEngine(MarketRules.model_validate(data)).config_hash != base
    assert SignalEngine(market_rules).config_hash == base


def test_screener_universe_and_liquidity(market_rules: MarketRules) -> None:
    assert Screener.universe(("bbca", "BBRI.JK"), ("BBCA", "TLKM")) == ("BBCA", "BBRI", "TLKM")
    from engine.indicators import compute_indicators

    check = Screener(market_rules).liquidity(
        compute_indicators(illiquid_frame().complete_only().frame)
    )
    assert check.eligible is False and check.avg_volume == Decimal("10000")
    ok = Screener(market_rules).liquidity(compute_indicators(flat_frame().complete_only().frame))
    assert (
        ok.eligible is True and ok.lookback_days == 20 and ok.value_basis == "approx close×volume"
    )


def test_zero_lot_card_is_still_reported_with_note(market_rules: MarketRules) -> None:
    engine = SignalEngine(market_rules, risk=RiskConfig(capital_example=Decimal("200000")))
    result = engine.run(END_SESSION, _universe())
    assert result.cards
    for c in result.cards:
        assert (
            c.risk.sizing is not None and c.risk.sizing.lots == 0 and "0 lot" in c.risk.sizing.note
        )


def test_fixture_origin_propagates(market_rules: MarketRules) -> None:
    result = SignalEngine(market_rules).run(END_SESSION, _universe())
    assert all(_universe()[c.symbol].frame.origin is DataOrigin.FIXTURE for c in result.cards)
