from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from data.providers.base import QualityStatus
from engine.indicators import cmf, compute_indicators, mfi, obv
from engine.models import StrategyState
from engine.money_flow import compute_money_flow
from engine.pipeline import SignalEngine, SymbolInput
from engine.strategies import MoneyFlowProxyStrategy
from engine.strategies.base import StrategyContext
from tests.engine_fixtures import END_SESSION, _frame_from_close, flat_frame, uptrend_frame


def accumulation_frame(symbol: str = "ACCM"):
    """Akumulasi realistis 12 sesi terakhir: 8 hari naik kecil dengan close dekat high dan volume
    ~1,8× rata-rata; 4 hari turun tipis dengan volume ~0,6× → OBV/CMF naik, harga hanya +2–3 %."""
    n = 300
    close = 1000.0 * (1.001 ** np.arange(n)) * (1 + 0.003 * np.sin(np.arange(n) / 4.0))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    volume = np.full(n, 6_000_000.0)
    pattern = [1, 1, -1, 1, 1, -1, 1, 1, -1, 1, 1, -1]  # 8 naik, 4 turun
    for offset, direction in zip(range(12, 0, -1), pattern, strict=True):
        i = n - offset
        open_[i] = close[i - 1]
        if direction > 0:
            close[i] = open_[i] * 1.004
            low[i] = open_[i] * 0.998
            high[i] = close[i] * 1.001  # close di puncak range
            volume[i] = 11_000_000.0
        else:
            close[i] = open_[i] * 0.998
            high[i] = open_[i] * 1.002
            low[i] = close[i] * 0.999
            volume[i] = 3_500_000.0
    return _frame_from_close(close, symbol=symbol, open_=open_, high=high, low=low, volume=volume)


def neutral_frame(symbol: str = "NEUT"):
    """Dibangun netral: naik/turun bergantian dengan besaran dan volume identik, close di tengah
    range → CMF≈0, OBV datar, hari akumulasi = distribusi = 0, rasio volume ≈ 1."""
    n = 300
    close = np.empty(n)
    close[0] = 1000.0
    for i in range(1, n):
        close[i] = close[i - 1] * (1.003 if i % 2 else 1 / 1.003)
    open_ = np.concatenate([[close[0]], close[:-1]])
    mid = (open_ + close) / 2
    high = mid + np.abs(close - open_) * 1.5 + 3
    low = mid - np.abs(close - open_) * 1.5 - 3
    volume = np.full(n, 6_000_000.0)
    return _frame_from_close(close, symbol=symbol, open_=open_, high=high, low=low, volume=volume)


def _ctx(frame) -> StrategyContext:
    ind = compute_indicators(frame.complete_only().frame)
    return StrategyContext(frame.symbol, END_SESSION, ind, QualityStatus.DEGRADED)


def test_money_flow_indicators_match_reference() -> None:
    rng = np.random.default_rng(1)
    n = 300
    close = pd.Series(1000 + rng.normal(0, 5, n).cumsum())
    high, low = close + rng.uniform(1, 8, n), close - rng.uniform(1, 8, n)
    vol = pd.Series(rng.integers(1_000_000, 5_000_000, n).astype(float))
    mfm = ((close - low) - (high - close)) / (high - low)
    ref_cmf = (mfm * vol).rolling(20).sum() / vol.rolling(20).sum()
    np.testing.assert_allclose(
        cmf(high, low, close, vol, 20).iloc[30:], ref_cmf.iloc[30:], atol=1e-10
    )
    ref_obv = (np.sign(close.diff()).fillna(0) * vol).cumsum()
    np.testing.assert_allclose(obv(close, vol).to_numpy(), ref_obv.to_numpy(), atol=1e-6)
    tp = (high + low + close) / 3
    rmf = tp * vol
    pos, neg = rmf.where(tp > tp.shift(), 0.0), rmf.where(tp < tp.shift(), 0.0)
    ref_mfi = 100 - 100 / (1 + pos.rolling(14).sum() / neg.rolling(14).sum())
    np.testing.assert_allclose(
        mfi(high, low, close, vol, 14).iloc[30:], ref_mfi.iloc[30:], atol=1e-8
    )
    ind = compute_indicators(
        pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": vol})
    )
    for col in ("cmf20", "obv", "obv_ema20", "mfi14"):
        assert ind.frame[col].iloc[:250].isna().all() and ind.frame[col].iloc[250:].notna().all()


def test_money_flow_stats_detect_accumulation_and_neutral() -> None:
    acc = compute_money_flow(compute_indicators(accumulation_frame().complete_only().frame), "ACCM")
    assert acc is not None
    assert acc.label == "akumulasi" and acc.score >= 25
    assert acc.cmf20 > Decimal("0.10") and acc.obv_slope_days > Decimal("2")
    assert acc.acc_days >= 5 and acc.dist_days == 0 and acc.updown_ratio > Decimal("1.3")
    assert acc.quiet is True and Decimal("0") < acc.price_change_pct <= Decimal("5")
    neutral = compute_money_flow(compute_indicators(neutral_frame().complete_only().frame), "NEUT")
    assert neutral is not None and neutral.label == "netral"
    assert set(acc.as_dict()) >= {"symbol", "label", "score", "cmf20", "obv_slope_days"}


def test_money_flow_stats_none_when_history_short() -> None:
    frame = flat_frame("FLAT")
    ind = compute_indicators(frame.complete_only().frame, warmup=len(frame) - 5)
    assert compute_money_flow(ind, "FLAT", window=10) is None


def test_proxy_strategy_signal_and_conditions() -> None:
    out = MoneyFlowProxyStrategy().outcome(_ctx(accumulation_frame()))
    assert out.state is StrategyState.SIGNAL and out.signal is not None
    ev = out.signal.evidence
    assert ev["data_basis"].startswith("proxy volume") and ev["quiet_accumulation"] is True
    assert "CMF20" in out.signal.reasons[0] and "OBV" in out.signal.reasons[1]
    assert out.signal.structure_stop_hint < out.signal.entry_low_hint <= out.signal.entry_high_hint
    # syarat dinaikkan → tidak ada setup (bukan inactive: data ada)
    strict = MoneyFlowProxyStrategy(min_cmf=0.9)
    assert strict.outcome(_ctx(accumulation_frame())).state is StrategyState.NO_SETUP
    assert (
        MoneyFlowProxyStrategy().outcome(_ctx(flat_frame("FLAT"))).state is StrategyState.NO_SETUP
    )
    assert MoneyFlowProxyStrategy().outcome(_ctx(uptrend_frame("UPTR"))).state in (
        StrategyState.NO_SETUP,
        StrategyState.SIGNAL,
    )


def test_pipeline_attaches_money_flow_and_report_lists_top(market_rules) -> None:
    from datetime import UTC, datetime

    from bot.formatter import format_report, validate_html
    from bot.reports import _money_flow_snapshots
    from core.snapshot import (
        GlobalContextSnapshot,
        NewsSnapshot,
        ReportType,
        SnapshotOrigin,
        build_snapshot,
    )
    from notifications.whatsapp_export import render_whatsapp

    engine = SignalEngine(market_rules)
    result = engine.run(
        END_SESSION,
        {
            "ACCM": SymbolInput(accumulation_frame(), QualityStatus.DEGRADED),
            "NEUT": SymbolInput(neutral_frame(), QualityStatus.DEGRADED),
        },
    )
    by = {e.symbol: e for e in result.evaluations}
    assert by["ACCM"].money_flow is not None and by["ACCM"].money_flow.label == "akumulasi"
    assert by["ACCM"].card is not None and by["ACCM"].card.primary_strategy == "money_flow_proxy"
    flows = _money_flow_snapshots(result)
    assert [m.symbol for m in flows] == ["ACCM"]  # netral (NEUT) tidak ditampilkan
    snap = build_snapshot(
        report_type=ReportType.MORNING,
        origin=SnapshotOrigin.FIXTURE,
        trading_date=date(2026, 3, 16),
        generated_at=datetime(2026, 3, 16, 1, 0, tzinfo=UTC),
        engine_result=result,
        strategy_versions=engine.strategy_versions,
        quality_by_symbol={"ACCM": QualityStatus.DEGRADED, "NEUT": QualityStatus.DEGRADED},
        providers_used=("fixture",),
        data_blocked=(),
        rules_label="CONTOH",
        calendar_label="CONTOH",
        global_context=GlobalContextSnapshot(status="unavailable", fetched_at=None),
        news=NewsSnapshot(status="not_configured"),
        entry_valid_sessions=3,
        money_flow=flows,
    )
    parts = format_report(snap)
    joined = "\n".join(parts)
    assert "💰 Money Flow" in joined and "bukan data broker" in joined and "▲ <b>ACCM</b>" in joined
    for p in parts:
        validate_html(p)
    wa = render_whatsapp(snap)
    assert "*💰 Money Flow*" in wa and "▲ *ACCM*" in wa
    assert "Smart Money Proxy (Volume)" in joined
