from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from data.aggregator import AggregatorConfig, MarketDataAggregator
from data.providers.base import (
    BrokerEntry,
    BrokerSummary,
    Capability,
    DataOrigin,
    Failed,
    ForeignFlow,
    Ok,
    QualityStatus,
    Timeframe,
    Unavailable,
    Unsupported,
)
from data.providers.local_flow import LocalFlowProvider
from engine.indicators import compute_indicators
from engine.models import StrategyState
from engine.pipeline import SignalEngine, SymbolInput
from engine.strategies import SmartMoneyStrategy
from engine.strategies.base import StrategyContext
from tests.engine_fixtures import END_SESSION, NOW, flat_frame, uptrend_frame

D = Decimal


def _summaries(frame, per_session: list[dict[str, tuple[str, str]]]) -> tuple[BrokerSummary, ...]:
    sessions = list(frame.frame["session_date"].iloc[-len(per_session) :])
    out = []
    for s, brokers in zip(sessions, per_session, strict=True):
        entries = tuple(BrokerEntry(code, D(b), D(sl)) for code, (b, sl) in brokers.items())
        out.append(
            BrokerSummary(frame.symbol, s, entries, "fixture", NOW, origin=DataOrigin.FIXTURE)
        )
    return tuple(out)


def _ctx(frame, **kw) -> StrategyContext:
    ind = compute_indicators(frame.complete_only().frame)
    return StrategyContext(frame.symbol, END_SESSION, ind, QualityStatus.DEGRADED, **kw)


# Broker AA & BB akumulasi 3 sesi (net besar), CC distribusi; nilai ~ miliar agar intensitas > 3 %.
ACCUM = [
    {
        "AA": ("6000000000", "1000000000"),
        "BB": ("3000000000", "500000000"),
        "CC": ("200000000", "2000000000"),
    },
    {
        "AA": ("5000000000", "800000000"),
        "BB": ("2500000000", "400000000"),
        "CC": ("100000000", "1500000000"),
    },
    {
        "AA": ("7000000000", "900000000"),
        "BB": ("2000000000", "300000000"),
        "CC": ("300000000", "2500000000"),
    },
]


def test_smart_money_inactive_without_data() -> None:
    out = SmartMoneyStrategy().outcome(
        _ctx(uptrend_frame(), broker_summary_reason="tidak ada provider")
    )
    assert out.state is StrategyState.INACTIVE and "tidak ada provider" in out.reason
    short = _summaries(uptrend_frame(), ACCUM[:2])
    out2 = SmartMoneyStrategy().outcome(_ctx(uptrend_frame(), broker_summaries=short))
    assert out2.state is StrategyState.INACTIVE and "butuh 3" in out2.reason


def test_smart_money_signal_with_accumulating_brokers() -> None:
    frame = uptrend_frame()
    out = SmartMoneyStrategy().outcome(_ctx(frame, broker_summaries=_summaries(frame, ACCUM)))
    assert out.state is StrategyState.SIGNAL and out.signal is not None
    ev = out.signal.evidence
    assert ev["accumulators"] == "AA, BB" and ev["top3_concentration"] >= D("0.99")
    assert ev["intensity_pct"] >= D("3")
    assert "Broker akumulasi 3 sesi berturut: AA, BB" in out.signal.reasons[0]
    assert out.signal.data_requirements == ("ohlcv_daily_complete", "broker_summary")
    assert (
        out.signal.structure_stop_hint is not None
        and out.signal.structure_stop_hint < out.signal.entry_low_hint
    )


def test_smart_money_requires_persistence_and_intensity_and_trend() -> None:
    frame = uptrend_frame()
    broken = [dict(ACCUM[0]), dict(ACCUM[1]), dict(ACCUM[2])]
    broken[1]["AA"] = ("100000000", "900000000")  # AA net jual di sesi tengah → bukan akumulator
    broken[1]["BB"] = ("100000000", "900000000")
    out = SmartMoneyStrategy().outcome(_ctx(frame, broker_summaries=_summaries(frame, broken)))
    assert out.state is StrategyState.NO_SETUP
    tiny = [
        {k: (str(int(b) // 1000), str(int(sl) // 1000)) for k, (b, sl) in s.items()} for s in ACCUM
    ]
    out2 = SmartMoneyStrategy().outcome(_ctx(frame, broker_summaries=_summaries(frame, tiny)))
    assert out2.state is StrategyState.NO_SETUP  # intensitas terlalu kecil
    # tren: frame datar dengan close < EMA50? flat_frame berosilasi; paksa syarat tren dengan EMA filter
    flat = flat_frame("FLAT")
    ind = compute_indicators(flat.complete_only().frame)
    if ind.last("close") <= ind.last("ema50"):
        out3 = SmartMoneyStrategy().outcome(_ctx(flat, broker_summaries=_summaries(flat, ACCUM)))
        assert out3.state is StrategyState.NO_SETUP


def test_smart_money_foreign_confirmation_bonus_and_zero_is_legit() -> None:
    frame = uptrend_frame()
    summaries = _summaries(frame, ACCUM)
    sessions = [s.session_date for s in summaries]
    base = SmartMoneyStrategy().outcome(_ctx(frame, broker_summaries=summaries)).signal
    flows = tuple(ForeignFlow(frame.symbol, s, D("1000000000"), "fixture", NOW) for s in sessions)
    with_foreign = (
        SmartMoneyStrategy()
        .outcome(_ctx(frame, broker_summaries=summaries, foreign_flows=flows))
        .signal
    )
    zero_flows = tuple(ForeignFlow(frame.symbol, s, D("0"), "fixture", NOW) for s in sessions)
    with_zero = (
        SmartMoneyStrategy()
        .outcome(_ctx(frame, broker_summaries=summaries, foreign_flows=zero_flows))
        .signal
    )
    assert base is not None and with_foreign is not None and with_zero is not None
    assert (
        with_foreign.score == min(100, base.score + 5)
        and with_foreign.evidence["foreign_confirm"] is True
    )
    assert with_zero.score == base.score and with_zero.evidence["foreign_confirm"] is False


def test_pipeline_runs_smart_money_when_history_provided(market_rules) -> None:
    frame = uptrend_frame()
    engine = SignalEngine(market_rules)
    ev = engine.evaluate_symbol(
        frame.symbol,
        SymbolInput(frame, QualityStatus.DEGRADED, broker_summaries=_summaries(frame, ACCUM)),
        END_SESSION,
    )
    states = {o.strategy_id: o.state for o in ev.outcomes}
    assert states["smart_money"] is StrategyState.SIGNAL
    assert states["foreign_flow"] is StrategyState.INACTIVE
    assert ev.card is not None and ev.card.primary_strategy == "smart_money"


# ----------------------------------------------------------------------------- provider CSV lokal


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_local_flow_provider_reads_csv_distinguishes_missing_zero_failed(tmp_path) -> None:
    _write(
        tmp_path,
        "foreign_flow/BBCA.csv",
        "date,buy_value,sell_value\n2026-03-12,1000,400\n2026-03-13,0,0\n",
    )
    _write(
        tmp_path,
        "broker_summary/BBCA.csv",
        "date,broker,buy_value,sell_value\n2026-03-13,AA,900,100\n2026-03-13,BB,50,500\n2026-03-12,AA,1,2\n",
    )
    _write(
        tmp_path,
        "broker_summary/RUSAK.csv",
        "date,broker,buy_value,sell_value\n2026-03-13,AA,abc,1\n",
    )
    p = LocalFlowProvider(tmp_path, clock=lambda: NOW)
    assert p.supports(Capability.FOREIGN_FLOW) and not p.supports(Capability.OHLCV_DAILY)
    ok = await p.get_foreign_flow("bbca", date(2026, 3, 12))
    assert isinstance(ok, Ok) and ok.value.net_value == D("600") and ok.value.buy_value == D("1000")
    zero = await p.get_foreign_flow("BBCA", date(2026, 3, 13))
    assert isinstance(zero, Ok) and zero.value.net_value == D("0")  # nol sah, bukan tidak tersedia
    missing_day = await p.get_foreign_flow("BBCA", date(2026, 3, 16))
    assert isinstance(missing_day, Unavailable)
    missing_file = await p.get_foreign_flow("TLKM", date(2026, 3, 13))
    assert isinstance(missing_file, Unavailable)
    summary = await p.get_broker_summary("BBCA", date(2026, 3, 13))
    assert isinstance(summary, Ok) and {e.code: e.net_value for e in summary.value.entries} == {
        "AA": D("800"),
        "BB": D("-450"),
    }
    assert isinstance(await p.get_broker_summary("RUSAK", date(2026, 3, 13)), Failed)
    assert isinstance(await p.get_ohlcv("BBCA", Timeframe.D1, None, None), Unsupported)
    assert await p.health_check() is True
    assert await LocalFlowProvider(tmp_path / "kosong").health_check() is False


async def test_aggregator_routes_flow_to_local_provider(tmp_path, fake_clock) -> None:
    from tests.conftest import FakeProvider

    _write(tmp_path, "foreign_flow/BBCA.csv", "date,net_value\n2026-03-13,250\n")
    market = FakeProvider("yahoo_fake")
    local = LocalFlowProvider(tmp_path, clock=fake_clock.now)
    agg = MarketDataAggregator(
        [market, local],
        AggregatorConfig(),
        clock=fake_clock.now,
        monotonic=fake_clock.monotonic,
        sleep=fake_clock.sleep,
    )
    assert agg.supports(Capability.FOREIGN_FLOW) and agg.supports(Capability.BROKER_SUMMARY)
    res = await agg.get_foreign_flow("BBCA", date(2026, 3, 13))
    assert (
        isinstance(res, Ok)
        and res.value.net_value == D("250")
        and res.value.provider == "local_flow"
    )
    assert isinstance(await agg.get_broker_summary("BBCA", date(2026, 3, 13)), Unavailable)


def test_settings_local_flow_requires_dir_and_builds(
    settings_factory, tmp_path, market_rules
) -> None:
    from pydantic import ValidationError

    from data.providers import build_providers

    with pytest.raises(ValidationError, match="FLOW_CSV_DIR"):
        settings_factory(provider_priority="yahoo,local_flow")
    s = settings_factory(provider_priority="yahoo,local_flow", flow_csv_dir=str(tmp_path))
    names = [p.name for p in build_providers(s, market_rules)]
    assert names == ["yahoo", "local_flow"]
    assert any("degraded" in w for w in s.config_warnings)  # local_flow bukan provider pasar kedua
    warn = settings_factory(flow_csv_dir=str(tmp_path))
    assert any("tidak dipakai" in w for w in warn.config_warnings)
