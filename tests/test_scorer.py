from __future__ import annotations

from decimal import Decimal

import pytest

from data.providers.base import QualityStatus
from engine.models import StrategyOutcome, StrategySignal, StrategyState
from engine.scorer import ScorerConfig, compute_confidence, rank_cards, select_cards


def _sig(sid: str, score: int) -> StrategyOutcome:
    signal = StrategySignal(
        strategy_id=sid,
        strategy_version="1",
        score=score,
        reasons=("r",),
        evidence={},
        data_requirements=("x",),
        entry_low_hint=Decimal("1"),
        entry_high_hint=Decimal("1"),
        structure_stop_hint=None,
    )
    return StrategyOutcome(sid, "1", StrategyState.SIGNAL, signal=signal)


def _none(sid: str, state: StrategyState = StrategyState.NO_SETUP) -> StrategyOutcome:
    return StrategyOutcome(sid, "1", state, reason="x")


def test_formula_primary_confluence_penalty() -> None:
    cfg = ScorerConfig()
    res = compute_confidence([_sig("a", 75), _sig("b", 65), _sig("c", 50)], QualityStatus.OK, cfg)
    assert res is not None
    assert res.primary_strategy == "a"
    assert res.breakdown["confluence"] == 10  # hanya b (>=60); c tidak dihitung
    assert res.breakdown["penalty"] == 0
    assert res.confidence == 85
    degraded = compute_confidence([_sig("a", 75), _sig("b", 65)], QualityStatus.DEGRADED, cfg)
    assert (
        degraded is not None and degraded.confidence == 75 and degraded.breakdown["penalty"] == 10
    )


def test_confluence_is_capped_and_inactive_contributes_zero() -> None:
    cfg = ScorerConfig()
    res = compute_confidence(
        [
            _sig("a", 70),
            _sig("b", 60),
            _sig("c", 60),
            _sig("d", 60),
            _none("e", StrategyState.INACTIVE),
        ],
        QualityStatus.OK,
        cfg,
    )
    assert res is not None and res.breakdown["confluence"] == 15 and res.confidence == 85
    only = compute_confidence(
        [_sig("a", 70), _none("b", StrategyState.INACTIVE), _none("c")], QualityStatus.OK, cfg
    )
    assert only is not None and only.confidence == 70  # data hilang tidak menaikkan confidence


def test_no_signal_returns_none() -> None:
    assert (
        compute_confidence(
            [_none("a"), _none("b", StrategyState.INSUFFICIENT_DATA)],
            QualityStatus.OK,
            ScorerConfig(),
        )
        is None
    )


def test_weights_affect_primary_and_confluence() -> None:
    cfg = ScorerConfig(weights={"a": Decimal("0.5"), "b": Decimal("1.2")})
    res = compute_confidence([_sig("a", 90), _sig("b", 70)], QualityStatus.OK, cfg)
    assert res is not None
    assert res.primary_strategy == "b"  # 70×1.2=84 > 90×0.5=45
    assert res.breakdown["effective:a"] == 45 and res.breakdown["effective:b"] == 84
    assert res.breakdown["confluence"] == 0  # a efektif 45 < 60
    assert res.confidence == 84


def test_tie_break_uses_strategy_order() -> None:
    res = compute_confidence([_sig("b", 70), _sig("a", 70)], QualityStatus.OK, ScorerConfig())
    assert res is not None and res.primary_strategy == "b"


def test_confidence_clamped() -> None:
    res = compute_confidence([_sig("a", 100), _sig("b", 100)], QualityStatus.OK, ScorerConfig())
    assert res is not None and res.confidence == 100


class _Risk:
    def __init__(self, rr: str) -> None:
        self.rr_tp1_net = Decimal(rr)


class _Card:
    def __init__(self, symbol: str, confidence: int, rr: str, liq: str) -> None:
        self.symbol = symbol
        self.confidence = confidence
        self.risk = _Risk(rr)
        self.liquidity_avg_value_20d = Decimal(liq)


def test_rank_and_select_tie_breaks_and_cap() -> None:
    cards = [
        _Card("ZZZ", 80, "2.1", "5"),
        _Card("AAA", 80, "2.1", "5"),
        _Card("BBB", 80, "2.5", "1"),
        _Card("CCC", 90, "2.0", "1"),
        _Card("DDD", 80, "2.1", "9"),
        _Card("EEE", 69, "9.9", "99"),
        _Card("FFF", 70, "1.0", "0"),
    ]
    ranked = rank_cards(cards)  # type: ignore[arg-type]
    assert [c.symbol for c in ranked] == ["CCC", "BBB", "DDD", "AAA", "ZZZ", "FFF", "EEE"]
    selected = select_cards(cards, ScorerConfig(threshold=70, max_signals=5))  # type: ignore[arg-type]
    assert [c.symbol for c in selected] == ["CCC", "BBB", "DDD", "AAA", "ZZZ"]
    assert "EEE" not in {c.symbol for c in selected}


def test_scorer_config_validation() -> None:
    with pytest.raises(ValueError):
        ScorerConfig(threshold=101)
    with pytest.raises(ValueError):
        ScorerConfig(max_signals=0)
    with pytest.raises(ValueError):
        ScorerConfig(weights={"a": Decimal("-1")})
