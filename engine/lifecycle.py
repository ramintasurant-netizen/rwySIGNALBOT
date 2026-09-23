"""Lifecycle sinyal (simulasi, bukan transaksi pengguna). Dipakai identik oleh live dan backtest.

Kebijakan (Tahap 3, disetujui):
- ``pending_entry`` berlaku ``entry_valid_sessions`` sesi setelah sesi publikasi; lewat itu → ``expired``.
- Entry terisi bila ``low <= entry_high`` pada sesi setelah publikasi. Harga isi = ``min(entry_high, open)``
  (gap turun ke dalam/lewat zona ⇒ terisi di open). Level tersentuh ≠ jaminan terisi di pasar nyata;
  ini asumsi simulasi yang dinyatakan.
- Pada bar entry: SL boleh terpicu (konservatif), TP tidak (urutan intrabar tidak diketahui).
- ``active``: SL diperiksa sebelum TP (konservatif). Gap melewati SL ⇒ keluar di open; gap melewati
  TP1 ⇒ keluar di open (lebih baik dari TP1). Tanpa partial TP: posisi ditutup penuh di TP1.
- ``active`` melampaui ``max_hold_sessions`` ⇒ ``closed_time`` di close bar tersebut.
- Bar parsial (intraday): hanya sentuhan high/low yang dievaluasi; keputusan berbasis close/expiry
  menunggu bar lengkap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class SignalStatus(StrEnum):
    PENDING_ENTRY = "pending_entry"
    ACTIVE = "active"
    CLOSED_TP = "closed_tp"
    CLOSED_SL = "closed_sl"
    CLOSED_TIME = "closed_time"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @property
    def is_open(self) -> bool:
        return self in (SignalStatus.PENDING_ENTRY, SignalStatus.ACTIVE)


@dataclass(frozen=True, slots=True)
class LifecycleConfig:
    entry_valid_sessions: int = 3
    max_hold_sessions: int = 20

    def __post_init__(self) -> None:
        if self.entry_valid_sessions < 1 or self.max_hold_sessions < 1:
            raise ValueError("entry_valid_sessions dan max_hold_sessions minimal 1")


@dataclass(frozen=True, slots=True)
class Bar:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    partial: bool = False  # bar intraday belum lengkap


@dataclass(frozen=True, slots=True)
class SignalState:
    status: SignalStatus
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    tp1: Decimal
    published_session: date
    filled_price: Decimal | None = None
    filled_session: date | None = None
    sessions_since_publish: int = 0  # sesi perdagangan yang sudah dievaluasi setelah publikasi
    sessions_held: int = 0


@dataclass(frozen=True, slots=True)
class Transition:
    previous: SignalStatus
    new: SignalStatus
    trigger_price: Decimal | None
    trigger_session: date
    note: str
    pnl_r: Decimal | None = None


def _pnl_r(state: SignalState, exit_price: Decimal) -> Decimal | None:
    if state.filled_price is None:
        return None
    r = state.entry_high - state.stop_loss
    if r <= 0:
        return None
    return ((exit_price - state.filled_price) / r).quantize(Decimal("0.01"))


def step(
    state: SignalState, bar: Bar, cfg: LifecycleConfig
) -> tuple[SignalState, Transition | None]:
    """Evaluasi satu bar SETELAH sesi publikasi. Mengembalikan state baru dan transisi (jika ada)."""
    if bar.session_date <= state.published_session:
        raise ValueError(
            "bar harus setelah sesi publikasi (tidak mengevaluasi TP/SL sebelum entry)"
        )
    if not state.status.is_open:
        return state, None

    if state.status is SignalStatus.PENDING_ENTRY:
        sessions = state.sessions_since_publish + (0 if bar.partial else 1)
        if bar.low <= state.entry_high:
            fill = min(state.entry_high, bar.open)
            filled = SignalState(
                status=SignalStatus.ACTIVE,
                entry_low=state.entry_low,
                entry_high=state.entry_high,
                stop_loss=state.stop_loss,
                tp1=state.tp1,
                published_session=state.published_session,
                filled_price=fill,
                filled_session=bar.session_date,
                sessions_since_publish=sessions,
                sessions_held=0 if bar.partial else 1,
            )
            note = f"entry terisi {fill} (min(entry_high, open))"
            if bar.low <= state.stop_loss:
                exit_price = (
                    min(state.stop_loss, bar.open)
                    if bar.open <= state.stop_loss
                    else state.stop_loss
                )
                closed = SignalState(**{**_asdict(filled), "status": SignalStatus.CLOSED_SL})
                return closed, Transition(
                    SignalStatus.PENDING_ENTRY,
                    SignalStatus.CLOSED_SL,
                    exit_price,
                    bar.session_date,
                    note + f"; SL tersentuh pada bar yang sama (konservatif), keluar {exit_price}",
                    _pnl_r(filled, exit_price),
                )
            return filled, Transition(
                SignalStatus.PENDING_ENTRY, SignalStatus.ACTIVE, fill, bar.session_date, note
            )
        if not bar.partial and sessions >= cfg.entry_valid_sessions:
            expired = SignalState(
                **{
                    **_asdict(state),
                    "status": SignalStatus.EXPIRED,
                    "sessions_since_publish": sessions,
                }
            )
            return expired, Transition(
                SignalStatus.PENDING_ENTRY,
                SignalStatus.EXPIRED,
                None,
                bar.session_date,
                f"entry tidak tersentuh dalam {cfg.entry_valid_sessions} sesi",
            )
        return SignalState(**{**_asdict(state), "sessions_since_publish": sessions}), None

    # ACTIVE
    held = state.sessions_held + (0 if bar.partial else 1)
    if bar.low <= state.stop_loss:
        exit_price = bar.open if bar.open < state.stop_loss else state.stop_loss
        note = (
            "gap turun melewati SL: keluar di open"
            if bar.open < state.stop_loss
            else "SL tersentuh"
        )
        closed = SignalState(
            **{**_asdict(state), "status": SignalStatus.CLOSED_SL, "sessions_held": held}
        )
        return closed, Transition(
            SignalStatus.ACTIVE,
            SignalStatus.CLOSED_SL,
            exit_price,
            bar.session_date,
            note,
            _pnl_r(state, exit_price),
        )
    if bar.high >= state.tp1:
        exit_price = bar.open if bar.open > state.tp1 else state.tp1
        note = (
            "gap naik melewati TP1: keluar di open"
            if bar.open > state.tp1
            else "TP1 tersentuh (tanpa partial)"
        )
        closed = SignalState(
            **{**_asdict(state), "status": SignalStatus.CLOSED_TP, "sessions_held": held}
        )
        return closed, Transition(
            SignalStatus.ACTIVE,
            SignalStatus.CLOSED_TP,
            exit_price,
            bar.session_date,
            note,
            _pnl_r(state, exit_price),
        )
    if not bar.partial and held >= cfg.max_hold_sessions:
        closed = SignalState(
            **{**_asdict(state), "status": SignalStatus.CLOSED_TIME, "sessions_held": held}
        )
        return closed, Transition(
            SignalStatus.ACTIVE,
            SignalStatus.CLOSED_TIME,
            bar.close,
            bar.session_date,
            f"masa tahan {cfg.max_hold_sessions} sesi habis: keluar di close",
            _pnl_r(state, bar.close),
        )
    return SignalState(**{**_asdict(state), "sessions_held": held}), None


def _asdict(state: SignalState) -> dict[str, object]:
    return {
        "status": state.status,
        "entry_low": state.entry_low,
        "entry_high": state.entry_high,
        "stop_loss": state.stop_loss,
        "tp1": state.tp1,
        "published_session": state.published_session,
        "filled_price": state.filled_price,
        "filled_session": state.filled_session,
        "sessions_since_publish": state.sessions_since_publish,
        "sessions_held": state.sessions_held,
    }
