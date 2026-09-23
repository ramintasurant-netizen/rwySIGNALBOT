from __future__ import annotations

from decimal import Decimal

import pytest

from config.market_rules import MarketRules
from engine.models import StrategySignal
from engine.risk import (
    RiskConfig,
    RiskRejected,
    build_risk_plan,
    gross_rr,
    is_on_tick,
    net_rr,
    position_size,
    price_limits,
    round_to_tick,
)

D = Decimal


def _signal(entry_low="985", entry_high="1000", structure="960") -> StrategySignal:
    return StrategySignal(
        strategy_id="t",
        strategy_version="1",
        score=80,
        reasons=("r",),
        evidence={},
        data_requirements=("ohlcv_daily_complete",),
        entry_low_hint=D(entry_low),
        entry_high_hint=D(entry_high),
        structure_stop_hint=D(structure) if structure is not None else None,
    )


# ----------------------------------------------------------------------------- tick rounding


@pytest.mark.parametrize(
    ("price", "mode", "expected"),
    [
        ("1003", "down", "1000"),
        ("1003", "up", "1005"),
        ("1002.4", "nearest", "1000"),
        ("1002.5", "nearest", "1005"),
        ("4999", "up", "5000"),  # naik melewati batas rentang 5000 (tick 10 → 25)
        ("5001", "down", "5000"),  # turun melewati batas rentang (tick 25 → 10)
        ("1999", "up", "2000"),
        ("2001", "down", "2000"),
        ("199.5", "up", "200"),
        ("201", "down", "200"),
        ("499", "up", "500"),
        ("0.4", "down", "1"),  # tidak pernah nol
        ("5012", "down", "5000"),
        ("5013", "up", "5025"),
    ],
)
def test_round_to_tick_repo_table(
    market_rules: MarketRules, price: str, mode: str, expected: str
) -> None:
    result = round_to_tick(D(price), market_rules, mode)  # type: ignore[arg-type]
    assert result == D(expected)
    assert is_on_tick(result, market_rules)


def test_round_to_tick_rerounds_when_band_ticks_are_inconsistent(market_rules: MarketRules) -> None:
    data = market_rules.model_dump(mode="json")
    data["tick_table"] = [
        {"min_price": 0, "max_price": 100, "tick": 3},
        {"min_price": 100, "max_price": None, "tick": 7},
    ]
    rules = MarketRules.model_validate(data)
    assert round_to_tick(D("99.5"), rules, "up") == D("105")  # 102 bukan kelipatan 7 → 105
    assert round_to_tick(D("101"), rules, "down") == D("96")  # 98 bukan kelipatan 3 → 96


def test_round_to_tick_rejects_nonpositive(market_rules: MarketRules) -> None:
    with pytest.raises(ValueError):
        round_to_tick(D("0"), market_rules, "down")


# ----------------------------------------------------------------------------- ARA/ARB


def test_price_limits_from_reference(market_rules: MarketRules) -> None:
    ara, arb = price_limits(D("1000"), market_rules)
    assert (ara, arb) == (D("1250"), D("850"))
    ara, arb = price_limits(D("150"), market_rules)  # band 35%/15%, tick 1/2
    assert (ara, arb) == (D("202"), D("128"))  # 202.5 → 202 (tick 2 di rentang 200+); 127.5 → 128
    ara, arb = price_limits(D("6000"), market_rules)  # band 20%/15%, tick 25
    assert (ara, arb) == (D("7200"), D("5100"))
    assert is_on_tick(ara, market_rules) and is_on_tick(arb, market_rules)


def test_arb_never_below_min_price(market_rules: MarketRules) -> None:
    _, arb = price_limits(D("55"), market_rules)
    assert arb == market_rules.price_limits.min_price == D("50")


# ----------------------------------------------------------------------------- rencana


def test_risk_plan_reference_numbers(market_rules: MarketRules) -> None:
    cfg = RiskConfig()
    plan = build_risk_plan(
        _signal(), atr=D("20"), reference_price=D("1000"), rules=market_rules, cfg=cfg
    )
    assert (plan.entry_low, plan.entry_high) == (D("985"), D("1000"))
    # SL = min(1000 − 1.5×20 = 970, struktur 960 − tick 5 = 955) = 955
    assert plan.stop_loss == D("955") and plan.r_value == D("45")
    # TP1: floor kotor 1090; syarat R:R bersih 2.0 → 1102.03 → dibulatkan ke atas 1105
    assert (plan.tp1, plan.tp2, plan.tp3) == (D("1105"), D("1150"), D("1195"))
    assert plan.rr_tp1_gross == D("2.33")
    assert plan.rr_tp1_net == D("2.06") and plan.rr_tp1_net >= cfg.min_rr
    assert (plan.ara, plan.arb) == (D("1250"), D("850"))
    assert plan.sizing is not None
    assert plan.sizing.risk_amount == D("1000000")
    assert plan.sizing.lots == 222 and plan.sizing.shares == 22200
    assert plan.sizing.notional == D("22200000")
    assert plan.notes == ()


def test_order_and_rr_hold_after_rounding_for_many_prices(market_rules: MarketRules) -> None:
    cfg = RiskConfig()
    for price in (
        "62",
        "155",
        "212",
        "497",
        "1005",
        "1998",
        "2010",
        "4995",
        "5025",
        "12350",
        "98750",
    ):
        p = D(price)
        atr = (p * D("0.02")).quantize(D("0.01"))
        sig = _signal(entry_low=str(p * D("0.99")), entry_high=str(p), structure=str(p * D("0.96")))
        plan = build_risk_plan(sig, atr=atr, reference_price=p, rules=market_rules, cfg=cfg)
        assert plan.stop_loss < plan.entry_low <= plan.entry_high < plan.tp1 <= plan.tp2 <= plan.tp3
        for level in (
            plan.entry_low,
            plan.entry_high,
            plan.stop_loss,
            plan.tp1,
            plan.tp2,
            plan.tp3,
        ):
            assert is_on_tick(level, market_rules), (price, level)
        assert plan.rr_tp1_gross >= cfg.min_rr and plan.rr_tp1_net >= cfg.min_rr
        assert plan.tp1 >= plan.entry_high + cfg.tp_multiples[0] * plan.r_value


def test_without_structure_stop_uses_atr(market_rules: MarketRules) -> None:
    plan = build_risk_plan(
        _signal(structure=None),
        atr=D("20"),
        reference_price=D("1000"),
        rules=market_rules,
        cfg=RiskConfig(),
    )
    assert plan.stop_loss == D("970")


def test_reject_when_stop_not_below_entry(market_rules: MarketRules) -> None:
    with pytest.raises(RiskRejected, match="tidak berada di bawah entry_low"):
        build_risk_plan(
            _signal(structure=None),
            atr=D("1"),
            reference_price=D("1000"),
            rules=market_rules,
            cfg=RiskConfig(),
        )


def test_reject_when_entry_outside_ara_arb(market_rules: MarketRules) -> None:
    with pytest.raises(RiskRejected, match="di luar batas ARA/ARB"):
        build_risk_plan(
            _signal("1300", "1320", "1200"),
            atr=D("20"),
            reference_price=D("1000"),
            rules=market_rules,
            cfg=RiskConfig(),
        )
    with pytest.raises(RiskRejected, match="di luar batas ARA/ARB"):
        build_risk_plan(
            _signal("800", "840", "700"),
            atr=D("20"),
            reference_price=D("1000"),
            rules=market_rules,
            cfg=RiskConfig(),
        )


def test_entry_clamped_to_ara_with_note(market_rules: MarketRules) -> None:
    plan = build_risk_plan(
        _signal("1240", "1260", "1150"),
        atr=D("20"),
        reference_price=D("1000"),
        rules=market_rules,
        cfg=RiskConfig(),
    )
    assert plan.entry_high == D("1250")
    assert any("di-clamp" in n and "ARA" in n for n in plan.notes)
    assert any("TP1" in n and "multi-sesi" in n for n in plan.notes)


def test_reject_zone_too_wide(market_rules: MarketRules) -> None:
    with pytest.raises(RiskRejected, match="lebih lebar"):
        build_risk_plan(
            _signal("950", "1000", "900"),
            atr=D("20"),
            reference_price=D("1000"),
            rules=market_rules,
            cfg=RiskConfig(),
        )


def test_reject_below_min_price_or_bad_atr(market_rules: MarketRules) -> None:
    with pytest.raises(RiskRejected, match="ATR"):
        build_risk_plan(
            _signal(), atr=D("0"), reference_price=D("1000"), rules=market_rules, cfg=RiskConfig()
        )
    with pytest.raises(RiskRejected, match="harga minimum"):
        build_risk_plan(
            _signal("48", "49", "40"),
            atr=D("1"),
            reference_price=D("49"),
            rules=market_rules,
            cfg=RiskConfig(),
        )


def test_min_rr_gate_after_fees_can_reject_when_targets_capped(market_rules: MarketRules) -> None:
    # tp_multiples kecil + biaya besar: TP1 dipaksa naik oleh syarat net R:R; tetap valid.
    cfg = RiskConfig(
        tp_multiples=(D("1"), D("1.5"), D("2")),
        min_rr=D("2.0"),
        fee_buy_pct=D("1"),
        fee_sell_pct=D("1"),
    )
    plan = build_risk_plan(
        _signal(), atr=D("20"), reference_price=D("1000"), rules=market_rules, cfg=cfg
    )
    assert plan.rr_tp1_net >= D("2.0")
    assert plan.tp1 > plan.entry_high + plan.r_value  # lebih tinggi dari floor kotor 1R


def test_gross_only_mode(market_rules: MarketRules) -> None:
    cfg = RiskConfig(apply_fees_to_rr=False)
    plan = build_risk_plan(
        _signal(), atr=D("20"), reference_price=D("1000"), rules=market_rules, cfg=cfg
    )
    assert plan.tp1 == D("1090")  # tepat entry + 2R, tanpa penyesuaian biaya
    assert plan.rr_tp1_gross == D("2.00") and plan.rr_tp1_net < D("2.0")


# ----------------------------------------------------------------------------- sizing


def test_position_size_zero_lots_note(market_rules: MarketRules) -> None:
    cfg = RiskConfig(capital_example=D("300000"), risk_pct=D("1"))
    sizing = position_size(D("1000"), D("955"), market_rules, cfg)
    assert sizing.lots == 0 and sizing.shares == 0 and sizing.notional == D("0")
    assert "0 lot" in sizing.note


def test_position_size_capped_by_capital(market_rules: MarketRules) -> None:
    cfg = RiskConfig(
        capital_example=D("10000000"), risk_pct=D("5")
    )  # risiko 500rb, R=5 → 100rb lembar
    sizing = position_size(D("1000"), D("995"), market_rules, cfg)
    assert sizing.lots == 100  # 10 juta / (1000×100)
    assert "dibatasi oleh modal" in sizing.note


def test_rr_helpers() -> None:
    assert gross_rr(D("1000"), D("950"), D("1100")) == D("2.00")
    assert gross_rr(D("1000"), D("1000"), D("1100")) == D("0")
    assert net_rr(
        D("1000"), D("950"), D("1100"), RiskConfig(fee_buy_pct=D("0"), fee_sell_pct=D("0"))
    ) == D("2.00")
    assert net_rr(D("1000"), D("950"), D("1100"), RiskConfig()) < D("2.00")


def test_risk_config_validation() -> None:
    with pytest.raises(ValueError):
        RiskConfig(tp_multiples=(D("3"), D("2"), D("4")))
    with pytest.raises(ValueError):
        RiskConfig(risk_pct=D("0"))
    with pytest.raises(ValueError):
        RiskConfig(fee_sell_pct=D("100"))
