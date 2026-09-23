"""Metrik backtest dengan definisi eksplisit untuk kasus tepi.

- ``trades`` = trade yang SELESAI (closed_tp/closed_sl/closed_time). Trade terbuka/kedaluwarsa
  dikeluarkan dan dihitung terpisah.
- ``win_rate`` = menang / trades (None bila 0 trade). Menang = pnl bersih > 0.
- ``expectancy_r`` = rata-rata PnL dalam R (None bila 0 trade). ``avg_r`` identik dengan
  expectancy (dipertahankan sebagai nama terpisah karena diminta laporan).
- ``profit_factor`` = gross profit / gross loss; None bila 0 trade; ``inf`` dilabeli
  ``profit_factor_note`` bila tidak ada kerugian.
- ``max_drawdown`` dihitung dari equity curve **berbasis waktu** (mark-to-market pada close
  setiap sesi, termasuk posisi terbuka), bukan dari penjumlahan trade menang/kalah.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

D0 = Decimal("0")


@dataclass(frozen=True, slots=True)
class Trade:
    symbol: str
    strategy: str
    strategy_version: str
    published_session: date
    entry_session: date
    exit_session: date
    entry_price: Decimal  # sudah termasuk slippage
    exit_price: Decimal  # sudah termasuk slippage
    stop_loss: Decimal
    tp1: Decimal
    shares: int
    fees: Decimal
    pnl: Decimal  # rupiah bersih setelah biaya & slippage
    # PnL bersih / (risiko rencana = (entry_high_rencana − SL) × shares). Denominator memakai rencana
    # (bukan harga isi) agar identik dengan lifecycle live; fill yang lebih baik memperkecil |R|.
    pnl_r: Decimal
    status: str  # closed_tp | closed_sl | closed_time
    confidence: int
    sessions_held: int
    exit_note: str


@dataclass(frozen=True, slots=True)
class EquityPoint:
    session: date
    equity: Decimal  # kas + nilai posisi (mark-to-market)
    cash: Decimal
    open_positions: int
    drawdown_pct: Decimal


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    trades: int
    wins: int
    losses: int
    win_rate: Decimal | None
    expectancy_r: Decimal | None
    avg_r: Decimal | None
    profit_factor: Decimal | None
    profit_factor_note: str
    gross_profit: Decimal
    gross_loss: Decimal
    net_pnl: Decimal
    total_fees: Decimal
    max_drawdown_pct: Decimal
    max_drawdown_amount: Decimal
    start_equity: Decimal
    end_equity: Decimal
    return_pct: Decimal
    open_at_end: int
    expired_signals: int
    signals_generated: int
    signals_not_filled: int
    avg_sessions_held: Decimal | None
    by_strategy: dict[str, dict[str, object]] = field(default_factory=dict)


def _q(value: Decimal, places: str = "0.01") -> Decimal:
    return value.quantize(Decimal(places))


def compute_metrics(
    trades: list[Trade],
    equity: list[EquityPoint],
    *,
    start_equity: Decimal,
    open_at_end: int,
    expired_signals: int,
    signals_generated: int,
) -> BacktestMetrics:
    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_profit = sum((t.pnl for t in wins), D0)
    gross_loss = -sum((t.pnl for t in losses), D0)
    net = sum((t.pnl for t in trades), D0)
    fees = sum((t.fees for t in trades), D0)

    if n == 0:
        pf, pf_note = None, "tidak ada trade selesai"
    elif gross_loss == 0:
        pf, pf_note = None, "tidak ada kerugian: profit factor tidak terdefinisi (∞)"
    else:
        pf, pf_note = _q(gross_profit / gross_loss), ""

    exp_r = _q(sum((t.pnl_r for t in trades), D0) / n) if n else None
    max_dd_pct, max_dd_amt = D0, D0
    peak = start_equity
    for point in equity:
        peak = max(peak, point.equity)
        dd_amt = peak - point.equity
        if dd_amt > max_dd_amt:
            max_dd_amt = dd_amt
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd_amt / peak * 100)
    end_equity = equity[-1].equity if equity else start_equity

    by_strategy: dict[str, dict[str, object]] = {}
    for sid in sorted({t.strategy for t in trades}):
        sub = [t for t in trades if t.strategy == sid]
        sub_wins = sum(1 for t in sub if t.pnl > 0)
        by_strategy[sid] = {
            "trades": len(sub),
            "win_rate": str(_q(Decimal(sub_wins) / len(sub) * 100, "0.1")),
            "expectancy_r": str(_q(sum((t.pnl_r for t in sub), D0) / len(sub))),
            "net_pnl": str(_q(sum((t.pnl for t in sub), D0))),
        }

    return BacktestMetrics(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        win_rate=_q(Decimal(len(wins)) / n * 100, "0.1") if n else None,
        expectancy_r=exp_r,
        avg_r=exp_r,
        profit_factor=pf,
        profit_factor_note=pf_note,
        gross_profit=_q(gross_profit),
        gross_loss=_q(gross_loss),
        net_pnl=_q(net),
        total_fees=_q(fees),
        max_drawdown_pct=_q(max_dd_pct),
        max_drawdown_amount=_q(max_dd_amt),
        start_equity=_q(start_equity),
        end_equity=_q(end_equity),
        return_pct=_q((end_equity - start_equity) / start_equity * 100) if start_equity else D0,
        open_at_end=open_at_end,
        expired_signals=expired_signals,
        signals_generated=signals_generated,
        signals_not_filled=expired_signals,
        avg_sessions_held=_q(Decimal(sum(t.sessions_held for t in trades)) / n, "0.1")
        if n
        else None,
        by_strategy=by_strategy,
    )


def write_equity_csv(points: list[EquityPoint], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["session", "equity", "cash", "open_positions", "drawdown_pct"])
        for p in points:
            writer.writerow(
                [p.session.isoformat(), p.equity, p.cash, p.open_positions, p.drawdown_pct]
            )


def write_trades_csv(trades: list[Trade], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "symbol",
        "strategy",
        "strategy_version",
        "published_session",
        "entry_session",
        "exit_session",
        "entry_price",
        "exit_price",
        "stop_loss",
        "tp1",
        "shares",
        "fees",
        "pnl",
        "pnl_r",
        "status",
        "confidence",
        "sessions_held",
        "exit_note",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for t in trades:
            writer.writerow([getattr(t, f) for f in fields])
