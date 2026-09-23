from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from engine.lifecycle import Bar, LifecycleConfig, SignalState, SignalStatus, step

D = Decimal
PUB = date(2026, 3, 13)
CFG = LifecycleConfig(entry_valid_sessions=3, max_hold_sessions=5)


def pending() -> SignalState:
    return SignalState(SignalStatus.PENDING_ENTRY, D("985"), D("1000"), D("955"), D("1105"), PUB)


def bar(d: int, o: str, h: str, l: str, c: str, partial: bool = False) -> Bar:  # noqa: E741
    return Bar(date(2026, 3, d), D(o), D(h), D(l), D(c), partial)


def test_no_evaluation_before_or_on_publish_session() -> None:
    with pytest.raises(ValueError):
        step(pending(), bar(13, "1000", "1010", "990", "1000"), CFG)


def test_entry_fill_at_min_entry_high_open() -> None:
    state, tr = step(pending(), bar(16, "1010", "1020", "995", "1015"), CFG)
    assert state.status is SignalStatus.ACTIVE and state.filled_price == D("1000")
    assert tr is not None and tr.new is SignalStatus.ACTIVE and tr.trigger_price == D("1000")
    # gap turun ke dalam zona: terisi di open
    state2, _ = step(pending(), bar(16, "990", "1000", "985", "995"), CFG)
    assert state2.filled_price == D("990")


def test_entry_bar_sl_conservative_but_no_tp_on_same_bar() -> None:
    # low menyentuh entry dan SL pada bar yang sama → SL dianggap lebih dulu
    state, tr = step(pending(), bar(16, "1000", "1010", "950", "1005"), CFG)
    assert (
        state.status is SignalStatus.CLOSED_SL and tr is not None and tr.trigger_price == D("955")
    )
    assert tr.pnl_r == D("-1.00")
    # high menyentuh TP1 pada bar entry → TIDAK ditutup (urutan tidak diketahui)
    state2, tr2 = step(pending(), bar(16, "1000", "1120", "995", "1110"), CFG)
    assert (
        state2.status is SignalStatus.ACTIVE and tr2 is not None and tr2.new is SignalStatus.ACTIVE
    )


def test_pending_expires_after_valid_sessions_and_partial_bars_do_not_count() -> None:
    s = pending()
    s, tr = step(s, bar(16, "1050", "1060", "1040", "1055"), CFG)
    assert tr is None and s.sessions_since_publish == 1
    s, tr = step(s, bar(17, "1050", "1060", "1040", "1055", partial=True), CFG)
    assert tr is None and s.sessions_since_publish == 1  # bar parsial tidak menambah hitungan
    s, tr = step(s, bar(17, "1050", "1060", "1040", "1055"), CFG)
    s, tr = step(s, bar(18, "1050", "1060", "1040", "1055"), CFG)
    assert s.status is SignalStatus.EXPIRED and tr is not None and tr.new is SignalStatus.EXPIRED


def test_active_sl_before_tp_and_gap_handling() -> None:
    active, _ = step(pending(), bar(16, "1000", "1005", "995", "1000"), CFG)
    # kedua level tersentuh: SL dahulu (konservatif)
    s, tr = step(active, bar(17, "1000", "1200", "900", "1100"), CFG)
    assert s.status is SignalStatus.CLOSED_SL and tr is not None and tr.trigger_price == D("955")
    # gap turun melewati SL: keluar di open
    s, tr = step(active, bar(17, "900", "950", "890", "920"), CFG)
    assert (
        tr is not None
        and tr.trigger_price == D("900")
        and "gap" in tr.note
        and tr.pnl_r == D("-2.22")
    )
    # TP1 tersentuh: keluar tepat TP1, tanpa partial
    s, tr = step(active, bar(17, "1050", "1110", "1040", "1100"), CFG)
    assert (
        s.status is SignalStatus.CLOSED_TP
        and tr is not None
        and tr.trigger_price == D("1105")
        and tr.pnl_r == D("2.33")
    )
    # gap naik melewati TP1: keluar di open (lebih baik)
    s, tr = step(active, bar(17, "1150", "1160", "1140", "1155"), CFG)
    assert tr is not None and tr.trigger_price == D("1150")


def test_active_time_exit_after_max_hold() -> None:
    # bar entry dihitung sebagai sesi tahan ke-1; max_hold 5 ⇒ keluar di close sesi ke-5
    s, _ = step(pending(), bar(16, "1000", "1005", "995", "1000"), CFG)
    assert s.sessions_held == 1
    for d in (17, 18, 19):
        s, tr = step(s, bar(d, "1000", "1010", "990", "1000"), CFG)
        assert tr is None
    assert s.sessions_held == 4
    s, tr = step(s, bar(20, "1000", "1010", "990", "1002"), CFG)
    assert s.status is SignalStatus.CLOSED_TIME and tr is not None and tr.trigger_price == D("1002")
    assert tr.pnl_r == D("0.04")


def test_partial_bar_touch_closes_but_does_not_count_sessions() -> None:
    active, _ = step(pending(), bar(16, "1000", "1005", "995", "1000"), CFG)
    s, tr = step(active, bar(17, "1000", "1110", "990", "1100", partial=True), CFG)
    assert s.status is SignalStatus.CLOSED_TP and s.sessions_held == 1


def test_closed_state_is_terminal() -> None:
    closed = SignalState(SignalStatus.CLOSED_SL, D("985"), D("1000"), D("955"), D("1105"), PUB)
    assert step(closed, bar(16, "1", "2", "0.5", "1"), CFG) == (closed, None)


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        LifecycleConfig(entry_valid_sessions=0)
