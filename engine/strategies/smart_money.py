"""Smart Money / Broker Akumulasi v1 (swing).

Aktif HANYA bila tersedia broker summary untuk ``sessions`` sesi berturut-turut yang berakhir pada
sesi evaluasi. Tanpa data ⇒ ``inactive`` (bukan sinyal, bukan skor).

Definisi (eksplisit, dapat diaudit lewat ``evidence``):
- Broker *akumulator* = broker dengan net buy > 0 pada SETIAP sesi dalam jendela.
- ``accum_net`` = Σ net buy akumulator selama jendela; ``intensity`` = accum_net / rata-rata nilai
  transaksi harian 20 sesi (%).
- ``concentration`` = pangsa top-3 akumulator terhadap total net buy positif seluruh broker (jendela).
- *Quiet accumulation*: |close_t / close_{t−N} − 1| ≤ ``max_price_move_pct`` — barang dikumpulkan
  tanpa harga lari (belum diprice-in).
- Konfirmasi tren minimum: close > EMA50. Foreign net buy pada ≥ separuh sesi ⇒ bonus.

Skor: 55 dasar + intensitas (0→0 %, 15 %→+20) + konsentrasi (40 %→0, 80 %→+15) + quiet +10 +
foreign +5. Entry zone [close − 0,5×ATR, close]; struktur stop = low terendah jendela.
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from data.providers.base import BrokerSummary
from engine.indicators import to_decimal
from engine.models import StrategySignal
from engine.strategies.base import InactiveStrategy, Strategy, StrategyContext, require_bars


class SmartMoneyStrategy(Strategy):
    id: ClassVar[str] = "smart_money"
    version: ClassVar[str] = "1.0.0"
    data_requirements: ClassVar[tuple[str, ...]] = ("ohlcv_daily_complete", "broker_summary")

    def __init__(
        self,
        *,
        sessions: int = 3,
        min_intensity_pct: float = 3.0,
        min_concentration: float = 0.4,
        max_price_move_pct: float = 4.0,
    ) -> None:
        self.sessions = sessions
        self.min_intensity_pct = min_intensity_pct
        self.min_concentration = min_concentration
        self.max_price_move_pct = max_price_move_pct

    def _window(self, ctx: StrategyContext) -> tuple[BrokerSummary, ...]:
        if ctx.broker_summaries is None:
            raise InactiveStrategy(
                ctx.broker_summary_reason or "data broker summary tidak tersedia"
            )
        history = sorted(ctx.broker_summaries, key=lambda b: b.session_date)
        if len(history) < self.sessions:
            raise InactiveStrategy(
                f"broker summary hanya {len(history)} sesi, butuh {self.sessions}"
            )
        window = tuple(history[-self.sessions :])
        if window[-1].session_date != ctx.session_date:
            raise InactiveStrategy(
                f"broker summary terakhir {window[-1].session_date} bukan sesi evaluasi {ctx.session_date}"
            )
        return window

    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None:
        require_bars(ctx, self.sessions + 2)
        window = self._window(ctx)
        per_session_net: list[dict[str, Decimal]] = [
            {e.code: e.net_value for e in summary.entries} for summary in window
        ]
        codes = set.intersection(*(set(m) for m in per_session_net)) if per_session_net else set()
        accumulators = sorted(c for c in codes if all(m[c] > 0 for m in per_session_net))
        if not accumulators:
            return None
        accum_net = sum((m[c] for m in per_session_net for c in accumulators), Decimal("0"))
        total_positive = sum(
            (v for m in per_session_net for v in m.values() if v > 0), Decimal("0")
        )
        if total_positive <= 0:
            return None
        by_accum = {c: sum((m[c] for m in per_session_net), Decimal("0")) for c in accumulators}
        top3 = sorted(by_accum.items(), key=lambda kv: kv[1], reverse=True)[:3]
        concentration = sum((v for _, v in top3), Decimal("0")) / total_positive

        ind = ctx.indicators
        close, ema50, atr = ind.last("close"), ind.last("ema50"), ind.last("atr14")
        avg_value = Decimal(repr(round(ind.last("value_avg20"), 0)))
        intensity = (accum_net / avg_value * 100) if avg_value > 0 else Decimal("0")
        if intensity < Decimal(str(self.min_intensity_pct)) or concentration < Decimal(
            str(self.min_concentration)
        ):
            return None
        if not close > ema50:
            return None

        prev_close = ind.last("close", self.sessions)
        price_move_pct = (close - prev_close) / prev_close * 100
        quiet = abs(price_move_pct) <= self.max_price_move_pct
        foreign_bonus = False
        if ctx.foreign_flows:
            flows = {f.session_date: f.net_value for f in ctx.foreign_flows}
            positives = sum(1 for s in window if flows.get(s.session_date, Decimal("0")) > 0)
            foreign_bonus = positives * 2 >= len(window)

        score = 55.0
        score += min(20.0, float(intensity) / 15.0 * 20.0)
        score += min(15.0, max(0.0, (float(concentration) - 0.4) / 0.4 * 15.0))
        if quiet:
            score += 10.0
        if foreign_bonus:
            score += 5.0

        reasons = [
            f"Broker akumulasi {self.sessions} sesi berturut: {', '.join(c for c, _ in top3)}"
            + (
                f" (+{len(accumulators) - len(top3)} lain)" if len(accumulators) > len(top3) else ""
            ),
            f"Net akumulasi ≈ {intensity:.1f}% dari rata-rata nilai transaksi harian; konsentrasi top-3 {concentration:.0%}",
            "Harga masih tenang (belum lari)"
            if quiet
            else f"Harga sudah bergerak {price_move_pct:.1f}% selama jendela",
            "Filter tren: close > EMA50",
        ]
        if foreign_bonus:
            reasons.append("Net foreign buy pada mayoritas sesi jendela")
        lows = float(ind.frame["low"].iloc[-self.sessions :].min())
        return self._signal(
            score=score,
            reasons=reasons,
            evidence={
                "close": to_decimal(close),
                "ema50": to_decimal(ema50),
                "atr14": to_decimal(atr),
                "accumulators": ", ".join(accumulators),
                "accum_net": accum_net,
                "intensity_pct": intensity.quantize(Decimal("0.01")),
                "top3_concentration": concentration.quantize(Decimal("0.01")),
                "price_move_pct": to_decimal(float(price_move_pct), 2),
                "quiet_accumulation": quiet,
                "foreign_confirm": foreign_bonus,
                "sessions": {s.session_date.isoformat(): len(s.entries) for s in window},
            },
            entry_low=close - 0.5 * atr,
            entry_high=close,
            structure_stop=lows,
        )
