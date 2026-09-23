"""Foreign/Broker Flow v1.

Aktif HANYA bila data foreign flow tersedia untuk ``consecutive_sessions`` sesi terakhir.
Nilai nol yang sah dihitung sebagai "bukan net buy" (bukan data hilang).

Syarat:
1. Net foreign buy > 0 pada ``consecutive_sessions`` sesi berturut-turut yang berakhir pada
   sesi evaluasi.
2. Filter tren minimal: close > EMA20.
3. Opsional: broker summary tersedia dan broker net buy teratas menyumbang >= ``dominant_share``
   dari total net buy positif (akumulasi terkonsentrasi) → skor tambahan.

Entry zone: [close − 0.5×ATR, close]. Struktur stop: min(low 5 bar, EMA20 − 0.5×ATR).
"""

from __future__ import annotations

from decimal import Decimal
from typing import ClassVar

from engine.indicators import to_decimal
from engine.models import StrategySignal
from engine.strategies.base import InactiveStrategy, Strategy, StrategyContext, require_bars


class ForeignFlowStrategy(Strategy):
    id: ClassVar[str] = "foreign_flow"
    version: ClassVar[str] = "1.0.0"
    data_requirements: ClassVar[tuple[str, ...]] = ("ohlcv_daily_complete", "foreign_flow")

    def __init__(self, *, consecutive_sessions: int = 3, dominant_share: float = 0.4) -> None:
        self.consecutive_sessions = consecutive_sessions
        self.dominant_share = dominant_share

    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None:
        require_bars(ctx, 6)
        if ctx.foreign_flows is None:
            raise InactiveStrategy(ctx.foreign_flow_reason or "data foreign flow tidak tersedia")
        flows = sorted(ctx.foreign_flows, key=lambda f: f.session_date)
        if len(flows) < self.consecutive_sessions:
            raise InactiveStrategy(
                f"foreign flow hanya {len(flows)} sesi, butuh {self.consecutive_sessions}"
            )
        recent = flows[-self.consecutive_sessions :]
        if recent[-1].session_date != ctx.session_date:
            raise InactiveStrategy(
                f"foreign flow terakhir {recent[-1].session_date} bukan sesi evaluasi {ctx.session_date}"
            )
        if not all(f.net_value > 0 for f in recent):
            return None

        ind = ctx.indicators
        close, ema20, atr = ind.last("close"), ind.last("ema20"), ind.last("atr14")
        if not close > ema20:
            return None

        total_net = sum((f.net_value for f in recent), Decimal("0"))
        avg_value_20d = Decimal(repr(round(ind.last("value_avg20"), 0)))
        intensity = (total_net / avg_value_20d * 100) if avg_value_20d > 0 else Decimal("0")

        reasons = [
            f"Net foreign buy {self.consecutive_sessions} sesi berturut-turut",
            f"Total net buy {total_net:,.0f} ≈ {intensity:.1f}% dari rata-rata nilai transaksi harian",
            "Filter tren: close > EMA20",
        ]
        evidence: dict[str, object] = {
            "close": to_decimal(close),
            "ema20": to_decimal(ema20),
            "atr14": to_decimal(atr),
            "net_foreign_total": total_net,
            "net_foreign_by_session": {str(f.session_date): str(f.net_value) for f in recent},
            "intensity_pct_of_avg_value": intensity.quantize(Decimal("0.01")),
        }
        # Skor: dasar 55; +hingga 25 untuk intensitas (0→0, 30% → 25); +10 bila broker dominan.
        score = 55.0 + min(25.0, float(intensity) / 30.0 * 25.0)
        if ctx.broker_summary is not None and ctx.broker_summary.entries:
            positives = [e for e in ctx.broker_summary.entries if e.net_value > 0]
            total_pos = sum((e.net_value for e in positives), Decimal("0"))
            if positives and total_pos > 0:
                top = max(positives, key=lambda e: e.net_value)
                share = top.net_value / total_pos
                evidence["dominant_broker"] = top.code
                evidence["dominant_broker_share"] = share.quantize(Decimal("0.01"))
                if share >= Decimal(str(self.dominant_share)):
                    score += 10.0
                    reasons.append(
                        f"Akumulasi terkonsentrasi: broker {top.code} {share:.0%} dari net buy"
                    )
        else:
            evidence["broker_summary"] = ctx.broker_summary_reason or "tidak tersedia"

        lows5 = float(ind.frame["low"].iloc[-5:].min())
        return self._signal(
            score=score,
            reasons=reasons,
            evidence=evidence,
            entry_low=close - 0.5 * atr,
            entry_high=close,
            structure_stop=min(lows5, ema20 - 0.5 * atr),
        )
