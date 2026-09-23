from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest

from backtest.gate import GateThresholds, evaluate_gate, gate_record, split_period
from backtest.metrics import EquityPoint, Trade, compute_metrics, write_equity_csv, write_trades_csv
from backtest.runner import BacktestConfig, BacktestResult, BacktestRunner
from bot.gates import evaluate_gates
from data.providers.base import OHLCVFrame, QualityStatus
from engine.lifecycle import LifecycleConfig
from engine.pipeline import SignalEngine
from engine.regime import RegimeConfig
from engine.scorer import ScorerConfig
from tests.engine_fixtures import (
    breakout_frame,
    flat_frame,
    trend_pullback_frame,
)

D = Decimal


# ----------------------------------------------------------------------------- fixture skenario


def _extend(frame: OHLCVFrame, future: list[tuple[float, float, float, float]]) -> OHLCVFrame:
    """Tambahkan bar lengkap setelah bar terakhir (hari kerja berikutnya) dengan OHLC eksplisit."""
    df = frame.frame
    last = df.index[-1]
    rows = []
    idx = []
    cursor = last
    for o, h, lo, c in future:
        cursor = cursor + pd.tseries.offsets.BDay(1)
        idx.append(cursor)
        rows.append({"open": o, "high": h, "low": lo, "close": c, "volume": 8_000_000.0})
    add = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    add["session_date"] = [ts.tz_convert("Asia/Jakarta").date() for ts in add.index]
    add["complete"] = True
    add["value"] = add["close"] * add["volume"]
    merged = pd.concat([df, add])
    return OHLCVFrame(
        symbol=frame.symbol,
        timeframe=frame.timeframe,
        price_basis=frame.price_basis,
        provider=frame.provider,
        fetched_at=frame.fetched_at,
        frame=merged,
        origin=frame.origin,
    )


def _engine(market_rules) -> SignalEngine:
    # Test mekanika eksekusi: filter rezim dimatikan (diuji terpisah di test_regime.py).
    return SignalEngine(
        market_rules, scorer=ScorerConfig(max_signals=5), regime=RegimeConfig(enabled=False)
    )


def _card_for(market_rules, frame: OHLCVFrame):
    from engine.pipeline import SymbolInput

    session = frame.frame["session_date"].iloc[-1]
    ev = _engine(market_rules).evaluate_symbol(
        frame.symbol, SymbolInput(frame, QualityStatus.DEGRADED), session
    )
    assert ev.card is not None, ev.blocked
    return ev.card, session


def _cfg(start: date, end: date, **kw) -> BacktestConfig:
    base = dict(
        initial_capital=D("100000000"), slippage_ticks=0, fee_buy_pct=D("0"), fee_sell_pct=D("0")
    )
    base.update(kw)
    return BacktestConfig(start=start, end=end, **base)


# ----------------------------------------------------------------------------- runner


def test_signal_fires_only_on_its_session_and_fills_next_bar_then_hits_tp(market_rules) -> None:
    base = breakout_frame("BRKO")
    card, signal_session = _card_for(market_rules, base)
    e_hi, tp1 = float(card.risk.entry_high), float(card.risk.tp1)
    # Bar berikutnya: turun ke zona entry (tanpa sentuh SL); lalu naik menembus TP1.
    frame = _extend(
        base, [(e_hi + 5, e_hi + 10, e_hi - 5, e_hi), (e_hi, tp1 + 20, e_hi - 2, tp1 + 10)]
    )
    sessions = list(frame.frame["session_date"].iloc[-3:])
    runner = BacktestRunner(
        _engine(market_rules),
        market_rules,
        _cfg(sessions[0], sessions[-1]),
        lifecycle=LifecycleConfig(),
    )
    result = runner.run({"BRKO": frame})
    assert result.sessions_evaluated == 3
    assert result.metrics.signals_generated >= 1
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.published_session == signal_session
    assert t.entry_session == sessions[1] and t.exit_session == sessions[2]
    assert t.entry_price == card.risk.entry_high and t.exit_price == card.risk.tp1
    assert t.status == "closed_tp" and t.pnl > 0
    assert t.pnl_r == card.risk.rr_tp1_gross  # tanpa biaya/slippage: PnL R = R:R kotor TP1
    assert t.shares == card.risk.sizing.shares
    assert result.metrics.trades == 1 and result.metrics.win_rate == D("100.0")
    assert (
        result.metrics.profit_factor is None
        and "tidak ada kerugian" in result.metrics.profit_factor_note
    )
    assert result.equity[-1].equity == result.config.initial_capital + t.pnl
    assert result.metrics.max_drawdown_pct >= 0


def test_no_lookahead_future_crash_does_not_change_signal_or_entry(market_rules) -> None:
    base = breakout_frame("BRKO")
    card, _ = _card_for(market_rules, base)
    e_hi, sl = float(card.risk.entry_high), float(card.risk.stop_loss)
    # Bar+1 terisi; bar+2 crash jauh di bawah SL (gap turun) → keluar di open (bukan di SL).
    crash_open = sl - 50
    frame = _extend(
        base,
        [
            (e_hi + 5, e_hi + 10, e_hi - 5, e_hi),
            (crash_open, crash_open + 5, crash_open - 10, crash_open - 5),
        ],
    )
    sessions = list(frame.frame["session_date"].iloc[-3:])
    result = BacktestRunner(
        _engine(market_rules), market_rules, _cfg(sessions[0], sessions[-1])
    ).run({"BRKO": frame})
    assert len(result.trades) == 1
    t = result.trades[0]
    assert t.status == "closed_sl" and "gap" in t.exit_note
    assert (
        t.exit_price == D(str(round(crash_open, 4))).normalize()
        or float(t.exit_price) == crash_open
    )
    assert t.pnl < 0 and t.pnl_r < D("-1")  # gap memperburuk melebihi 1R
    # Sinyal tetap lahir pada sesi yang sama meski masa depan buruk (tidak ada lookahead).
    assert t.published_session == sessions[0]


def test_same_bar_touch_of_entry_and_sl_is_conservative_loss(market_rules) -> None:
    base = breakout_frame("BRKO")
    card, _ = _card_for(market_rules, base)
    e_hi, sl, tp1 = float(card.risk.entry_high), float(card.risk.stop_loss), float(card.risk.tp1)
    frame = _extend(
        base, [(e_hi, tp1 + 50, sl - 5, tp1 + 40)]
    )  # entry, SL, dan TP tersentuh dalam satu bar
    sessions = list(frame.frame["session_date"].iloc[-2:])
    result = BacktestRunner(
        _engine(market_rules), market_rules, _cfg(sessions[0], sessions[-1])
    ).run({"BRKO": frame})
    assert len(result.trades) == 1 and result.trades[0].status == "closed_sl"
    assert result.trades[0].pnl_r == D("-1.00")


def test_pending_expires_without_fill_and_is_not_a_trade(market_rules) -> None:
    base = breakout_frame("BRKO")
    card, _ = _card_for(market_rules, base)
    e_hi = float(card.risk.entry_high)
    away = e_hi + 200
    frame = _extend(base, [(away, away + 5, away - 5, away)] * 4)
    sessions = list(frame.frame["session_date"].iloc[-5:])
    result = BacktestRunner(
        _engine(market_rules),
        market_rules,
        _cfg(sessions[0], sessions[-1]),
        lifecycle=LifecycleConfig(entry_valid_sessions=3),
    ).run({"BRKO": frame})
    assert result.trades == []
    assert result.metrics.expired_signals >= 1 and result.metrics.signals_not_filled >= 1
    assert (
        result.metrics.trades == 0
        and result.metrics.win_rate is None
        and result.metrics.expectancy_r is None
    )
    assert (
        result.metrics.profit_factor is None
        and result.metrics.profit_factor_note == "tidak ada trade selesai"
    )
    assert result.equity[-1].equity == result.config.initial_capital


def test_fees_and_slippage_reduce_pnl_and_are_recorded(market_rules) -> None:
    base = breakout_frame("BRKO")
    card, _ = _card_for(market_rules, base)
    e_hi, tp1 = float(card.risk.entry_high), float(card.risk.tp1)
    frame = _extend(
        base, [(e_hi + 5, e_hi + 10, e_hi - 5, e_hi), (e_hi, tp1 + 20, e_hi - 2, tp1 + 10)]
    )
    sessions = list(frame.frame["session_date"].iloc[-3:])
    gross = BacktestRunner(
        _engine(market_rules), market_rules, _cfg(sessions[0], sessions[-1])
    ).run({"BRKO": frame})
    net = BacktestRunner(
        _engine(market_rules),
        market_rules,
        _cfg(
            sessions[0],
            sessions[-1],
            slippage_ticks=1,
            fee_buy_pct=D("0.15"),
            fee_sell_pct=D("0.25"),
        ),
    ).run({"BRKO": frame})
    g, n = gross.trades[0], net.trades[0]
    assert n.entry_price > g.entry_price and n.exit_price < g.exit_price  # slippage memburuk
    assert n.fees > 0 and g.fees == 0
    assert n.pnl < g.pnl and n.pnl_r < g.pnl_r
    expected_fees = (n.entry_price * n.shares * D("0.0015")).quantize(D("1")) + (
        n.exit_price * n.shares * D("0.0025")
    ).quantize(D("1"))
    assert n.fees == expected_fees
    assert net.metrics.total_fees == n.fees


def test_capital_cap_and_one_position_per_symbol(market_rules) -> None:
    base = breakout_frame("BRKO")
    card, _ = _card_for(market_rules, base)
    e_hi, tp1 = float(card.risk.entry_high), float(card.risk.tp1)
    frame = _extend(
        base,
        [
            (e_hi + 5, e_hi + 10, e_hi - 5, e_hi),
            (e_hi, e_hi + 5, e_hi - 5, e_hi + 2),
            (e_hi, tp1 + 20, e_hi - 2, tp1 + 10),
        ],
    )
    sessions = list(frame.frame["session_date"].iloc[-4:])
    # Kas kecil: lot dibatasi kas (entry ≈ 1080 → 1 lot = 108.000; kas 300.000 ⇒ 2 lot),
    # lebih kecil dari rencana risiko engine.
    small = BacktestRunner(
        _engine(market_rules),
        market_rules,
        _cfg(sessions[0], sessions[-1], initial_capital=D("300000")),
    ).run({"BRKO": frame})
    assert small.trades
    planned = card.risk.sizing.shares if card.risk.sizing else 0
    assert 0 < small.trades[0].shares < planned
    assert small.trades[0].shares * card.risk.entry_high <= D("300000")
    assert all(p.open_positions <= 1 for p in small.equity)
    tiny = BacktestRunner(
        _engine(market_rules),
        market_rules,
        _cfg(sessions[0], sessions[-1], initial_capital=D("50000")),
    ).run({"BRKO": frame})
    assert tiny.trades == [] and any("sizing 0 lot" in n for n in tiny.notes)


def test_max_open_positions_limits_new_signals(market_rules) -> None:
    frames = {"BRKO": breakout_frame("BRKO"), "TRND": trend_pullback_frame("TRND")}
    session = frames["BRKO"].frame["session_date"].iloc[-1]
    result = BacktestRunner(
        _engine(market_rules), market_rules, _cfg(session, session, max_open_positions=1)
    ).run(frames)
    assert result.metrics.signals_generated == 1
    both = BacktestRunner(
        _engine(market_rules), market_rules, _cfg(session, session, max_open_positions=5)
    ).run(frames)
    assert both.metrics.signals_generated == 2
    assert any("menunggu entry" in n for n in both.notes)


def test_no_sessions_with_enough_history_raises(market_rules) -> None:
    frame = flat_frame("FLAT")
    early = frame.frame["session_date"].iloc[10]
    with pytest.raises(ValueError, match="histori"):
        BacktestRunner(_engine(market_rules), market_rules, _cfg(early, early)).run({"FLAT": frame})
    with pytest.raises(ValueError, match="tidak ada data"):
        BacktestRunner(_engine(market_rules), market_rules, _cfg(early, early)).run({})


def test_result_is_deterministic_and_hash_binds_config(market_rules) -> None:
    base = breakout_frame("BRKO")
    card, _ = _card_for(market_rules, base)
    e_hi, tp1 = float(card.risk.entry_high), float(card.risk.tp1)
    frame = _extend(
        base, [(e_hi + 5, e_hi + 10, e_hi - 5, e_hi), (e_hi, tp1 + 20, e_hi - 2, tp1 + 10)]
    )
    sessions = list(frame.frame["session_date"].iloc[-3:])
    a = BacktestRunner(_engine(market_rules), market_rules, _cfg(sessions[0], sessions[-1])).run(
        {"BRKO": frame}
    )
    b = BacktestRunner(_engine(market_rules), market_rules, _cfg(sessions[0], sessions[-1])).run(
        {"BRKO": frame}
    )
    assert a.summary() == b.summary() and a.result_hash == b.result_hash
    c = BacktestRunner(
        _engine(market_rules), market_rules, _cfg(sessions[0], sessions[-1], slippage_ticks=2)
    ).run({"BRKO": frame})
    assert c.result_hash != a.result_hash
    assert a.config_hash == _engine(market_rules).config_hash
    assert a.limitations and any("survivorship" in x for x in a.limitations)


# ----------------------------------------------------------------------------- metrik


def _trade(pnl: str, pnl_r: str, status: str = "closed_tp", strategy: str = "s") -> Trade:
    d0 = date(2026, 1, 5)
    return Trade(
        "X",
        strategy,
        "1",
        d0,
        d0,
        d0 + timedelta(days=1),
        D("1000"),
        D("1100"),
        D("950"),
        D("1100"),
        100,
        D("0"),
        D(pnl),
        D(pnl_r),
        status,
        80,
        2,
        "",
    )


def test_metrics_definitions_and_edge_cases() -> None:
    eq = [
        EquityPoint(date(2026, 1, i), D(v), D(v), 0, D("0"))
        for i, v in zip(range(5, 10), ("100", "120", "90", "95", "130"), strict=True)
    ]
    m = compute_metrics(
        [
            _trade("20", "2"),
            _trade("-10", "-1", "closed_sl"),
            _trade("-5", "-0.5", "closed_sl", "t"),
        ],
        eq,
        start_equity=D("100"),
        open_at_end=1,
        expired_signals=2,
        signals_generated=6,
    )
    assert m.trades == 3 and m.wins == 1 and m.losses == 2
    assert m.win_rate == D("33.3")
    assert m.expectancy_r == D("0.17") and m.avg_r == m.expectancy_r
    assert m.profit_factor == D("1.33")  # 20 / 15
    assert m.gross_profit == D("20.00") and m.gross_loss == D("15.00") and m.net_pnl == D("5.00")
    # drawdown dari equity curve: puncak 120 → 90 = 25%, bukan dari penjumlahan trade
    assert m.max_drawdown_pct == D("25.00") and m.max_drawdown_amount == D("30.00")
    assert m.end_equity == D("130.00") and m.return_pct == D("30.00")
    assert m.open_at_end == 1 and m.expired_signals == 2 and m.signals_generated == 6
    assert set(m.by_strategy) == {"s", "t"} and m.by_strategy["s"]["trades"] == 2

    empty = compute_metrics(
        [], eq, start_equity=D("100"), open_at_end=0, expired_signals=0, signals_generated=0
    )
    assert (
        empty.trades == 0
        and empty.win_rate is None
        and empty.profit_factor is None
        and empty.expectancy_r is None
    )
    only_wins = compute_metrics(
        [_trade("10", "1")],
        eq,
        start_equity=D("100"),
        open_at_end=0,
        expired_signals=0,
        signals_generated=1,
    )
    assert only_wins.profit_factor is None and "tidak ada kerugian" in only_wins.profit_factor_note


def test_csv_writers(tmp_path) -> None:
    eq = [EquityPoint(date(2026, 1, 5), D("100"), D("100"), 0, D("0"))]
    write_equity_csv(eq, tmp_path / "eq.csv")
    write_trades_csv([_trade("1", "0.1")], tmp_path / "tr.csv")
    assert (tmp_path / "eq.csv").read_text().splitlines()[
        0
    ] == "session,equity,cash,open_positions,drawdown_pct"
    assert "closed_tp" in (tmp_path / "tr.csv").read_text()


# ----------------------------------------------------------------------------- gate


def _result_with(metrics_kwargs: dict, market_rules) -> BacktestResult:
    from backtest.metrics import BacktestMetrics

    base = dict(
        trades=40,
        wins=24,
        losses=16,
        win_rate=D("60.0"),
        expectancy_r=D("0.5"),
        avg_r=D("0.5"),
        profit_factor=D("1.8"),
        profit_factor_note="",
        gross_profit=D("10"),
        gross_loss=D("5"),
        net_pnl=D("5"),
        total_fees=D("1"),
        max_drawdown_pct=D("8"),
        max_drawdown_amount=D("8"),
        start_equity=D("100"),
        end_equity=D("105"),
        return_pct=D("5"),
        open_at_end=0,
        expired_signals=3,
        signals_generated=43,
        signals_not_filled=3,
        avg_sessions_held=D("4.0"),
    )
    base.update(metrics_kwargs)
    engine = _engine(market_rules)
    cfg = BacktestConfig(start=date(2025, 1, 1), end=date(2025, 12, 31))
    return BacktestResult(
        config=cfg,
        engine_version="0.3.0",
        config_hash=engine.config_hash,
        strategy_versions=engine.strategy_versions,
        universe=("BRKO",),
        sessions_evaluated=200,
        first_session=date(2025, 1, 2),
        last_session=date(2025, 12, 30),
        trades=[],
        equity=[],
        metrics=BacktestMetrics(**base),
        data_provider="fixture",
        data_origin="fixture",
    )


def test_split_period_time_based() -> None:
    (is_s, is_e), (oos_s, oos_e) = split_period(date(2025, 1, 1), date(2025, 12, 31), D("0.3"))
    assert is_s == date(2025, 1, 1) and oos_e == date(2025, 12, 31)
    assert oos_s == is_e + timedelta(days=1)
    assert 100 <= (oos_e - oos_s).days + 1 <= 110
    with pytest.raises(ValueError):
        split_period(date(2025, 1, 1), date(2025, 1, 1), D("0.3"))


def test_gate_pass_and_fail_reasons(market_rules) -> None:
    th = GateThresholds()
    good = evaluate_gate(_result_with({}, market_rules), th)
    assert good.passed and good.failures == []
    few = evaluate_gate(_result_with({"trades": 10}, market_rules), th)
    assert not few.passed and any("minimum 30" in f for f in few.failures)
    neg = evaluate_gate(_result_with({"expectancy_r": D("-0.1")}, market_rules), th)
    assert not neg.passed and any("expectancy" in f for f in neg.failures)
    low_pf = evaluate_gate(_result_with({"profit_factor": D("1.1")}, market_rules), th)
    assert not low_pf.passed
    deep_dd = evaluate_gate(_result_with({"max_drawdown_pct": D("20")}, market_rules), th)
    assert not deep_dd.passed and any("drawdown" in f for f in deep_dd.failures)
    no_loss_enough = evaluate_gate(
        _result_with(
            {
                "profit_factor": None,
                "profit_factor_note": "tidak ada kerugian",
                "gross_loss": D("0"),
            },
            market_rules,
        ),
        th,
    )
    assert no_loss_enough.passed
    no_trades = evaluate_gate(
        _result_with(
            {
                "trades": 0,
                "profit_factor": None,
                "expectancy_r": None,
                "win_rate": None,
                "profit_factor_note": "tidak ada trade selesai",
            },
            market_rules,
        ),
        th,
    )
    assert not no_trades.passed


def test_gate_record_binds_versions_and_unlocks_runtime_gate(
    market_rules, calendar, settings_factory
) -> None:
    result = _result_with({}, market_rules)
    evaluation = evaluate_gate(result, GateThresholds())
    record = gate_record(result, evaluation, now=datetime(2026, 3, 16, tzinfo=UTC))
    assert record["passed"] is True and record["config_hash"] == result.config_hash
    assert record["strategy_versions"] == result.strategy_versions
    assert "tidak menjamin" in record["disclaimer"]
    engine = _engine(market_rules)
    g = evaluate_gates(
        settings_factory(),
        market_rules,
        calendar,
        date(2026, 3, 16),
        paused=False,
        backtest_gate=record,
        strategy_versions=engine.strategy_versions,
        config_hash=engine.config_hash,
        target_verifications=None,
    )
    assert not any("gate backtest" in w for w in g.warnings)
    other_engine = SignalEngine(market_rules, scorer=ScorerConfig(threshold=80))
    g2 = evaluate_gates(
        settings_factory(),
        market_rules,
        calendar,
        date(2026, 3, 16),
        paused=False,
        backtest_gate=record,
        strategy_versions=other_engine.strategy_versions,
        config_hash=other_engine.config_hash,
        target_verifications=None,
    )
    assert any("config_hash berbeda" in w for w in g2.warnings)
    failed_record = gate_record(
        result, evaluate_gate(_result_with({"trades": 1}, market_rules), GateThresholds())
    )
    assert failed_record["passed"] is False


def test_gate_thresholds_validation() -> None:
    with pytest.raises(ValueError):
        GateThresholds(min_trades=0)
    with pytest.raises(ValueError):
        GateThresholds(oos_fraction=D("1"))
