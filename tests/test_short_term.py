from __future__ import annotations

from decimal import Decimal

import numpy as np

from engine.short_term import DISCLAIMER, compute_short_term_stats, rank_short_term
from tests.engine_fixtures import _frame_from_close, flat_frame


def _gap_frame(symbol: str, gap: float, intraday: float, n: int = 300):
    """Open = close_prev × (1+gap); close = open × (1+intraday) — statistik yang diketahui."""
    close = np.empty(n)
    open_ = np.empty(n)
    close[0] = 1000.0
    open_[0] = 1000.0
    for i in range(1, n):
        open_[i] = close[i - 1] * (1 + gap)
        close[i] = open_[i] * (1 + intraday)
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    return _frame_from_close(close, symbol=symbol, open_=open_, high=high, low=low)


def test_stats_match_known_construction() -> None:
    frame = _gap_frame("GAPU", gap=0.004, intraday=-0.001)
    st = compute_short_term_stats(frame, lookback=60)
    assert st is not None and st.sessions == 60
    # close dibulatkan ke tick 5 → statistik mendekati konstruksi, bukan persis
    assert Decimal("0.2") < st.overnight_avg_pct < Decimal("0.6")
    assert st.overnight_win_rate >= Decimal("90")
    assert st.intraday_avg_pct < Decimal("0.1")
    assert st.overnight_t > st.intraday_t
    assert st.liquid is True and st.atr_pct > 0


def test_ranking_by_style_filters_liquidity_and_negative_edge() -> None:
    bsjp_good = compute_short_term_stats(_gap_frame("GAPU", 0.004, -0.001))
    bpjs_good = compute_short_term_stats(_gap_frame("INTR", -0.001, 0.004))
    flat = compute_short_term_stats(flat_frame("FLAT"))
    illiquid = compute_short_term_stats(
        _gap_frame("ILLQ", 0.01, 0.0), min_avg_value=Decimal("1e15")
    )
    stats = [s for s in (bsjp_good, bpjs_good, flat, illiquid) if s is not None]
    bsjp = rank_short_term(stats, "bsjp", top=5)
    bpjs = rank_short_term(stats, "bpjs", top=5)
    assert bsjp and bsjp[0].symbol == "GAPU" and "ILLQ" not in {s.symbol for s in bsjp}
    assert bpjs and bpjs[0].symbol == "INTR"
    assert all(s.overnight_avg_pct > 0 for s in bsjp) and all(s.intraday_avg_pct > 0 for s in bpjs)
    assert rank_short_term(stats, "bsjp", top=1) == bsjp[:1]


def test_short_history_returns_none_and_disclaimer_present() -> None:
    frame = flat_frame("FLAT")
    from dataclasses import replace

    short = replace(frame, frame=frame.frame.iloc[-50:])
    assert compute_short_term_stats(short, lookback=60) is None
    assert "bukan prediksi" in DISCLAIMER
