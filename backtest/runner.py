"""Runner backtest: engine + lifecycle produksi, evaluasi per sesi tanpa lookahead.

Model eksekusi (eksplisit, konservatif):
- Pada setiap sesi t (bar lengkap), engine dijalankan dengan frame yang dipotong ke bar ≤ t
  (``SignalEngine.evaluate_symbol`` memotong sendiri; runner juga memotong frame sebagai pertahanan
  ganda). Kartu yang lahir di sesi t hanya boleh terisi mulai sesi t+1 (``lifecycle.step``
  menolak bar ≤ sesi publikasi).
- Fill entry: ``min(entry_high, open)`` + slippage; SL/TP: sesuai ``lifecycle.step`` (SL sebelum TP,
  gap ⇒ open) ± slippage. Level tersentuh ≠ jaminan terisi di pasar nyata (antrean/likuiditas) —
  dinyatakan sebagai keterbatasan.
- Biaya beli/jual persen dari nilai transaksi; sizing lot penuh dari ``RiskConfig`` dibatasi kas
  tersedia; satu posisi per simbol; maksimum ``max_open_positions``.
- Kapasitas sinyal per sesi mengikuti ``ScorerConfig.max_signals`` (seleksi engine yang sama).
- Equity mark-to-market pada close setiap sesi (posisi terbuka dinilai di close).
- Strategi berbasis foreign flow/intraday TIDAK dievaluasi dari data EOD (tidak ada data ⇒ inactive).

Keterbatasan yang dilaporkan: survivorship bias (universe = watchlist saat ini), aksi korporasi
(harga Yahoo split-adjusted saja), satu provider, tanpa antrean/partial fill, tanpa pajak selain
yang dimodelkan sebagai biaya persen.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_FLOOR, Decimal
from typing import Any

from backtest.metrics import BacktestMetrics, EquityPoint, Trade, compute_metrics
from config.market_rules import MarketRules
from data.providers.base import OHLCVFrame, QualityStatus
from engine import ENGINE_VERSION
from engine.lifecycle import Bar, LifecycleConfig, SignalState, SignalStatus, step
from engine.models import SignalCard
from engine.pipeline import SignalEngine, SymbolInput
from engine.regime import compute_regime
from engine.risk import round_to_tick

LIMITATIONS = (
    "Universe adalah watchlist saat ini (survivorship bias): saham yang delisting/keluar tidak terwakili.",
    "Harga historis Yahoo disesuaikan split (bukan dividen); aksi korporasi lain tidak dimodelkan.",
    "Satu provider data (tanpa cross-validation); kualitas dianggap 'degraded'.",
    "Level tersentuh dianggap terisi penuh (tanpa antrean/partial fill/likuiditas intrabar).",
    "Urutan sentuhan intrabar tidak diketahui: SL diprioritaskan sebelum TP (konservatif).",
    "Biaya dan slippage adalah parameter CONTOH; sesuaikan dengan broker Anda.",
    "Strategi foreign flow/intraday tidak dievaluasi dari data EOD (inactive).",
)


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    start: date
    end: date
    initial_capital: Decimal = Decimal("100000000")
    slippage_ticks: int = 1
    max_open_positions: int = 5
    fee_buy_pct: Decimal = Decimal("0.15")
    fee_sell_pct: Decimal = Decimal("0.25")
    warmup_bars: int = 250

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("end < start")
        if self.initial_capital <= 0 or self.slippage_ticks < 0 or self.max_open_positions < 1:
            raise ValueError("konfigurasi backtest tidak valid")

    def as_dict(self) -> dict[str, str | int]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "initial_capital": str(self.initial_capital),
            "slippage_ticks": self.slippage_ticks,
            "max_open_positions": self.max_open_positions,
            "fee_buy_pct": str(self.fee_buy_pct),
            "fee_sell_pct": str(self.fee_sell_pct),
            "warmup_bars": self.warmup_bars,
        }


@dataclass
class _Position:
    card: SignalCard
    state: SignalState
    shares: int
    entry_fill: Decimal | None = None
    entry_fee: Decimal = Decimal("0")
    entry_session: date | None = None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    config: BacktestConfig
    engine_version: str
    config_hash: str
    strategy_versions: dict[str, str]
    universe: tuple[str, ...]
    sessions_evaluated: int
    first_session: date | None
    last_session: date | None
    trades: list[Trade]
    equity: list[EquityPoint]
    metrics: BacktestMetrics
    data_provider: str
    data_origin: str
    limitations: tuple[str, ...] = LIMITATIONS
    notes: tuple[str, ...] = ()

    @property
    def result_hash(self) -> str:
        payload = json.dumps(
            {
                "config": self.config.as_dict(),
                "engine_config_hash": self.config_hash,
                "universe": self.universe,
                "trades": [
                    (t.symbol, t.entry_session.isoformat(), str(t.pnl)) for t in self.trades
                ],
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def summary(self) -> dict[str, Any]:
        m = self.metrics
        return {
            "period": {
                "start": self.config.start.isoformat(),
                "end": self.config.end.isoformat(),
                "first_session": self.first_session,
                "last_session": self.last_session,
                "sessions_evaluated": self.sessions_evaluated,
            },
            "universe": list(self.universe),
            "data": {"provider": self.data_provider, "origin": self.data_origin},
            "engine": {
                "version": self.engine_version,
                "config_hash": self.config_hash,
                "strategy_versions": self.strategy_versions,
            },
            "backtest_config": self.config.as_dict(),
            "metrics": {
                "trades": m.trades,
                "wins": m.wins,
                "losses": m.losses,
                "win_rate_pct": m.win_rate,
                "expectancy_r": m.expectancy_r,
                "avg_r": m.avg_r,
                "profit_factor": m.profit_factor,
                "profit_factor_note": m.profit_factor_note,
                "net_pnl": m.net_pnl,
                "total_fees": m.total_fees,
                "max_drawdown_pct": m.max_drawdown_pct,
                "max_drawdown_amount": m.max_drawdown_amount,
                "return_pct": m.return_pct,
                "start_equity": m.start_equity,
                "end_equity": m.end_equity,
                "open_at_end": m.open_at_end,
                "signals_generated": m.signals_generated,
                "signals_not_filled": m.signals_not_filled,
                "avg_sessions_held": m.avg_sessions_held,
                "by_strategy": m.by_strategy,
            },
            "limitations": list(self.limitations),
            "notes": list(self.notes),
            "result_hash": self.result_hash,
        }


def _dec(v: object) -> Decimal:
    return Decimal(repr(float(v)))  # type: ignore[arg-type]


class BacktestRunner:
    def __init__(
        self,
        engine: SignalEngine,
        rules: MarketRules,
        cfg: BacktestConfig,
        *,
        lifecycle: LifecycleConfig | None = None,
        quality: QualityStatus = QualityStatus.DEGRADED,
        progress: Callable[[date, int, int], None] | None = None,
    ) -> None:
        self.engine = engine
        self.rules = rules
        self.cfg = cfg
        self.lifecycle = lifecycle or LifecycleConfig()
        self.quality = quality
        self._progress = progress

    # ------------------------------------------------------------------ util harga
    def _slip(self, price: Decimal, direction: int) -> Decimal:
        """Slippage ``slippage_ticks`` tick memburuk: beli naik (+1), jual turun (−1)."""
        if self.cfg.slippage_ticks == 0:
            return price
        tick = self.rules.tick_for(price)
        adjusted = price + direction * tick * self.cfg.slippage_ticks
        adjusted = max(adjusted, self.rules.price_limits.min_price)
        return round_to_tick(adjusted, self.rules, "up" if direction > 0 else "down")

    def _size(self, card: SignalCard, cash: Decimal) -> int:
        """Lot penuh dari rencana risiko engine, dibatasi kas tersedia (termasuk biaya beli)."""
        planned = card.risk.sizing.shares if card.risk.sizing else 0
        if planned <= 0:
            return 0
        entry = self._slip(card.risk.entry_high, +1)
        cost_per_share = entry * (1 + self.cfg.fee_buy_pct / 100)
        affordable_lots = int(
            (cash / (cost_per_share * self.rules.lot_size)).to_integral_value(rounding=ROUND_FLOOR)
        )
        lots = min(planned // self.rules.lot_size, affordable_lots)
        return max(0, lots) * self.rules.lot_size

    # ------------------------------------------------------------------ jalur utama
    def run(
        self, frames: dict[str, OHLCVFrame], *, index_frame: OHLCVFrame | None = None
    ) -> BacktestResult:
        if not frames:
            raise ValueError("tidak ada data untuk backtest")
        regime_counts: dict[str, int] = {}
        provider = next(iter(frames.values())).provider
        origin = next(iter(frames.values())).origin.value
        # Sesi kandidat = gabungan session_date bar lengkap dalam [start, end] yang punya cukup histori.
        sessions: set[date] = set()
        for frame in frames.values():
            df = frame.frame
            complete = df.loc[df["complete"].astype(bool)]
            dates = list(complete["session_date"])
            for i, d in enumerate(dates):
                if self.cfg.start <= d <= self.cfg.end and i + 1 > self.cfg.warmup_bars:
                    sessions.add(d)
        ordered = sorted(sessions)
        if not ordered:
            raise ValueError(
                f"tidak ada sesi dengan histori ≥ {self.cfg.warmup_bars + 1} bar dalam {self.cfg.start}..{self.cfg.end}"
            )

        cash = self.cfg.initial_capital
        positions: dict[str, _Position] = {}
        trades: list[Trade] = []
        equity: list[EquityPoint] = []
        peak = cash
        signals_generated = 0
        expired = 0
        notes: list[str] = []

        for idx, session in enumerate(ordered):
            if self._progress:
                self._progress(session, idx + 1, len(ordered))
            # 1) Lifecycle posisi/pending dengan bar sesi ini (dipublikasikan sebelum sesi ini).
            for symbol in list(positions):
                pos = positions[symbol]
                bar = _bar_at(frames[symbol], session)
                if bar is None:
                    continue
                if bar.session_date <= pos.state.published_session:
                    continue
                new_state, transition = step(pos.state, bar, self.lifecycle)
                pos.state = new_state
                if transition is None:
                    continue
                if transition.new is SignalStatus.ACTIVE:
                    fill = self._slip(transition.trigger_price or new_state.entry_high, +1)
                    pos.entry_fill = fill
                    pos.entry_fee = (fill * pos.shares * self.cfg.fee_buy_pct / 100).quantize(
                        Decimal("1")
                    )
                    pos.entry_session = session
                    cash -= fill * pos.shares + pos.entry_fee
                    continue
                if transition.new is SignalStatus.EXPIRED:
                    expired += 1
                    del positions[symbol]
                    continue
                # closed_*: bila transisi langsung dari pending (SL pada bar entry), isi entry dulu.
                if transition.previous is SignalStatus.PENDING_ENTRY and pos.entry_fill is None:
                    fill = self._slip(new_state.filled_price or new_state.entry_high, +1)
                    pos.entry_fill = fill
                    pos.entry_fee = (fill * pos.shares * self.cfg.fee_buy_pct / 100).quantize(
                        Decimal("1")
                    )
                    pos.entry_session = session
                    cash -= fill * pos.shares + pos.entry_fee
                exit_price = self._slip(transition.trigger_price or bar.close, -1)
                exit_fee = (exit_price * pos.shares * self.cfg.fee_sell_pct / 100).quantize(
                    Decimal("1")
                )
                cash += exit_price * pos.shares - exit_fee
                assert pos.entry_fill is not None and pos.entry_session is not None
                pnl = (exit_price - pos.entry_fill) * pos.shares - pos.entry_fee - exit_fee
                risk_per_share = pos.card.risk.entry_high - pos.card.risk.stop_loss
                pnl_r = (
                    (pnl / (risk_per_share * pos.shares)).quantize(Decimal("0.01"))
                    if risk_per_share > 0
                    else Decimal("0")
                )
                trades.append(
                    Trade(
                        symbol=symbol,
                        strategy=pos.card.primary_strategy,
                        strategy_version=pos.card.strategy_versions.get(
                            pos.card.primary_strategy, "?"
                        ),
                        published_session=pos.state.published_session,
                        entry_session=pos.entry_session,
                        exit_session=session,
                        entry_price=pos.entry_fill,
                        exit_price=exit_price,
                        stop_loss=pos.card.risk.stop_loss,
                        tp1=pos.card.risk.tp1,
                        shares=pos.shares,
                        fees=pos.entry_fee + exit_fee,
                        pnl=pnl.quantize(Decimal("1")),
                        pnl_r=pnl_r,
                        status=new_state.status.value,
                        confidence=pos.card.confidence,
                        sessions_held=new_state.sessions_held,
                        exit_note=transition.note,
                    )
                )
                del positions[symbol]

            # 2) Engine pada sesi ini (bar ≤ sesi; kartu baru hanya terisi mulai sesi berikutnya).
            universe: dict[str, SymbolInput] = {}
            for symbol, frame in frames.items():
                if symbol in positions:
                    continue  # satu posisi per simbol
                sliced = _slice_to(frame, session)
                if sliced is None:
                    continue
                universe[symbol] = SymbolInput(
                    sliced,
                    self.quality,
                    foreign_flow_reason="backtest EOD: tidak ada data foreign flow",
                )
            capacity = self.cfg.max_open_positions - len(positions)
            if universe and capacity > 0:
                regime = None
                if self.engine.regime.enabled:
                    regime = compute_regime(index_frame, session, self.engine.regime)
                    key = regime.regime.value
                    regime_counts[key] = regime_counts.get(key, 0) + 1
                result = self.engine.run(session, universe, regime=regime)
                for card in result.cards[:capacity]:
                    signals_generated += 1
                    shares = self._size(card, cash)
                    if shares <= 0:
                        notes_key = "sizing_zero"
                        if notes_key not in notes:
                            notes.append(notes_key)
                        continue
                    positions[card.symbol] = _Position(
                        card=card,
                        state=SignalState(
                            SignalStatus.PENDING_ENTRY,
                            card.risk.entry_low,
                            card.risk.entry_high,
                            card.risk.stop_loss,
                            card.risk.tp1,
                            session,
                        ),
                        shares=shares,
                    )

            # 3) Equity mark-to-market di close sesi ini.
            mtm = cash
            for symbol, pos in positions.items():
                if pos.entry_fill is None:
                    continue
                bar = _bar_at(frames[symbol], session)
                mtm += (bar.close if bar else pos.entry_fill) * pos.shares
            peak = max(peak, mtm)
            dd = ((peak - mtm) / peak * 100).quantize(Decimal("0.01")) if peak > 0 else Decimal("0")
            equity.append(
                EquityPoint(
                    session,
                    mtm.quantize(Decimal("1")),
                    cash.quantize(Decimal("1")),
                    sum(1 for p in positions.values() if p.entry_fill is not None),
                    dd,
                )
            )

        open_at_end = sum(1 for p in positions.values() if p.entry_fill is not None)
        pending_at_end = sum(1 for p in positions.values() if p.entry_fill is None)
        clean_notes = tuple(
            n
            if n != "sizing_zero"
            else "beberapa kartu dilewati: sizing 0 lot (modal/kas tidak cukup)"
            for n in notes
        )
        if pending_at_end:
            clean_notes = (
                *clean_notes,
                f"{pending_at_end} sinyal masih menunggu entry di akhir periode (tidak dihitung)",
            )
        if self.engine.regime.enabled:
            if index_frame is None:
                clean_notes = (
                    *clean_notes,
                    "filter rezim aktif tetapi data indeks tidak diberikan: semua sesi 'unknown'",
                )
            summary = ", ".join(f"{k}={v}" for k, v in sorted(regime_counts.items()))
            clean_notes = (
                *clean_notes,
                f"rezim {self.engine.regime.index_symbol} per sesi: {summary or '-'}",
            )
        metrics = compute_metrics(
            trades,
            equity,
            start_equity=self.cfg.initial_capital,
            open_at_end=open_at_end,
            expired_signals=expired,
            signals_generated=signals_generated,
        )
        return BacktestResult(
            config=self.cfg,
            engine_version=ENGINE_VERSION,
            config_hash=self.engine.config_hash,
            strategy_versions=self.engine.strategy_versions,
            universe=tuple(sorted(frames)),
            sessions_evaluated=len(ordered),
            first_session=ordered[0],
            last_session=ordered[-1],
            trades=trades,
            equity=equity,
            metrics=metrics,
            data_provider=provider,
            data_origin=origin,
            notes=clean_notes,
        )


def _bar_at(frame: OHLCVFrame, session: date) -> Bar | None:
    df = frame.frame
    rows = df.loc[(df["session_date"] == session) & df["complete"].astype(bool)]
    if rows.empty:
        return None
    r = rows.iloc[-1]
    return Bar(
        session, _dec(r["open"]), _dec(r["high"]), _dec(r["low"]), _dec(r["close"]), partial=False
    )


def _slice_to(frame: OHLCVFrame, session: date) -> OHLCVFrame | None:
    """Frame hanya dengan bar lengkap ≤ sesi (pertahanan anti-lookahead di sisi runner)."""
    df = frame.frame
    mask = df["complete"].astype(bool) & (df["session_date"] <= session)
    sliced = df.loc[mask]
    if sliced.empty or sliced["session_date"].iloc[-1] != session:
        return None
    return replace(frame, frame=sliced)
