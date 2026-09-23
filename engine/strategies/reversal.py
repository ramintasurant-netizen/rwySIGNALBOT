"""Reversal v1 (bullish divergence + konfirmasi).

Algoritme eksplisit:
1. Pivot low harga dengan ``pivot_left``/``pivot_right`` bar (``confirmed_pivot_lows``); pivot
   pada posisi i hanya sah bila bar i+pivot_right sudah tersedia (tidak memakai bar masa depan).
2. Dua pivot terakhir (P1 lebih lama, P2 lebih baru) dalam ``lookback`` bar; jarak >= ``min_gap``.
3. Divergence bullish: low[P2] < low[P1] dan RSI[P2] > RSI[P1].
4. RSI oversold: min(RSI) di antara P1..P2 <= ``oversold``.
5. Konfirmasi candle di support: bar terakhir bullish (close > open), close > close sebelumnya,
   low bar terakhir >= low[P2] × (1 − ``support_tolerance``) dan bar terakhir maksimal
   ``max_bars_after_pivot`` bar setelah konfirmasi P2.

Entry zone: [max(low[-1], close − 0.5×ATR), close]. Struktur stop: low[P2].
"""

from __future__ import annotations

from typing import ClassVar

from engine.indicators import confirmed_pivot_lows, to_decimal
from engine.models import StrategySignal
from engine.strategies.base import Strategy, StrategyContext, require_bars


class ReversalStrategy(Strategy):
    id: ClassVar[str] = "reversal"
    version: ClassVar[str] = "1.0.0"

    def __init__(
        self,
        *,
        lookback: int = 40,
        pivot_left: int = 3,
        pivot_right: int = 2,
        min_gap: int = 5,
        oversold: float = 30.0,
        support_tolerance: float = 0.01,
        max_bars_after_pivot: int = 3,
    ) -> None:
        self.lookback = lookback
        self.pivot_left = pivot_left
        self.pivot_right = pivot_right
        self.min_gap = min_gap
        self.oversold = oversold
        self.support_tolerance = support_tolerance
        self.max_bars_after_pivot = max_bars_after_pivot

    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None:
        require_bars(ctx, self.lookback + self.pivot_left + self.pivot_right)
        ind = ctx.indicators
        window = ind.frame.iloc[-self.lookback :]
        pivots = confirmed_pivot_lows(window["low"], self.pivot_left, self.pivot_right)
        if len(pivots) < 2:
            return None
        p1, p2 = pivots[-2], pivots[-1]
        if p2 - p1 < self.min_gap:
            return None
        last = len(window) - 1
        # P2 dikonfirmasi pada bar p2+pivot_right; sinyal hanya berlaku beberapa bar setelahnya.
        confirmed_at = p2 + self.pivot_right
        if last - confirmed_at > self.max_bars_after_pivot:
            return None

        low1, low2 = float(window["low"].iloc[p1]), float(window["low"].iloc[p2])
        rsi1, rsi2 = float(window["rsi14"].iloc[p1]), float(window["rsi14"].iloc[p2])
        if not (low2 < low1 and rsi2 > rsi1):
            return None
        rsi_min = float(window["rsi14"].iloc[p1 : p2 + 1].min())
        if not rsi_min <= self.oversold:
            return None

        close, open_, low, prev_close = (
            ind.last("close"),
            ind.last("open"),
            ind.last("low"),
            ind.last("close", 1),
        )
        atr = ind.last("atr14")
        if not (close > open_ and close > prev_close):
            return None
        if low < low2 * (1 - self.support_tolerance):
            return None

        divergence_pts = rsi2 - rsi1
        # Skor: dasar 55; +hingga 20 untuk kedalaman oversold (30→0, 20→20); +hingga 15 untuk
        # divergence RSI (0→0, 10 poin→15); +10 bila close menutup di atas open pivot P2.
        score = 55.0
        score += min(20.0, max(0.0, (self.oversold - rsi_min) / 10.0 * 20.0))
        score += min(15.0, divergence_pts / 10.0 * 15.0)
        if close > float(window["open"].iloc[p2]):
            score += 10.0

        return self._signal(
            score=score,
            reasons=[
                f"Bullish divergence: low {low2:.0f} < {low1:.0f} sementara RSI {rsi2:.1f} > {rsi1:.1f}",
                f"RSI14 oversold minimum {rsi_min:.1f} <= {self.oversold:.0f}",
                "Candle konfirmasi bullish di atas support pivot",
            ],
            evidence={
                "close": to_decimal(close),
                "pivot1_low": to_decimal(low1),
                "pivot2_low": to_decimal(low2),
                "pivot1_rsi": to_decimal(rsi1, 2),
                "pivot2_rsi": to_decimal(rsi2, 2),
                "rsi_min": to_decimal(rsi_min, 2),
                "bars_since_confirmation": int(last - confirmed_at),
                "atr14": to_decimal(atr),
            },
            entry_low=max(low, close - 0.5 * atr),
            entry_high=close,
            structure_stop=low2,
        )
