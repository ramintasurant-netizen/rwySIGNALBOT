"""Trend Pullback v1.

Syarat (semua pada bar lengkap terakhir, indeks -1):
1. Tren naik: close > EMA50 > EMA200 dan close > EMA200.
2. Pullback: dalam ``pullback_window`` bar terakhir, low menyentuh/mendekati EMA20
   (low <= EMA20 × (1 + ``touch_tolerance``)) dan close hari ini masih di atas EMA50.
3. RSI14 pada rentang [40, 55] dan berbalik naik (rsi[-1] > rsi[-2]).
4. Konfirmasi: close[-1] > close[-2].

Entry zone: [max(EMA20, close − 0.5×ATR), close]. Struktur stop: min(low pullback, EMA50).
"""

from __future__ import annotations

from typing import ClassVar

from engine.indicators import to_decimal
from engine.models import StrategySignal
from engine.strategies.base import Strategy, StrategyContext, require_bars


class TrendPullbackStrategy(Strategy):
    id: ClassVar[str] = "trend_pullback"
    version: ClassVar[str] = "1.0.0"

    def __init__(
        self,
        *,
        pullback_window: int = 5,
        touch_tolerance: float = 0.005,
        rsi_low: float = 40.0,
        rsi_high: float = 55.0,
    ) -> None:
        self.pullback_window = pullback_window
        self.touch_tolerance = touch_tolerance
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high

    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None:
        require_bars(ctx, self.pullback_window + 2)
        ind = ctx.indicators
        close, ema20, ema50, ema200 = (
            ind.last("close"),
            ind.last("ema20"),
            ind.last("ema50"),
            ind.last("ema200"),
        )
        rsi_now, rsi_prev = ind.last("rsi14"), ind.last("rsi14", 1)
        atr = ind.last("atr14")
        prev_close = ind.last("close", 1)

        if not (close > ema50 > ema200):
            return None
        window = ind.frame.iloc[-self.pullback_window :]
        touched = (window["low"] <= window["ema20"] * (1 + self.touch_tolerance)).any()
        if not touched:
            return None
        if not (self.rsi_low <= rsi_now <= self.rsi_high and rsi_now > rsi_prev):
            return None
        if not close > prev_close:
            return None

        pullback_low = float(window["low"].min())
        distance_to_ema20_pct = (close - ema20) / ema20 * 100
        trend_strength_pct = (ema50 - ema200) / ema200 * 100
        # Skor: dasar 60; +hingga 15 untuk kedekatan ke EMA20 (<=3%); +hingga 15 untuk
        # kekuatan tren (EMA50 vs EMA200 hingga 10%); +10 bila RSI di paruh bawah rentang.
        score = 60.0
        score += max(0.0, 15.0 * (1 - min(abs(distance_to_ema20_pct), 3.0) / 3.0))
        score += min(15.0, trend_strength_pct * 1.5) if trend_strength_pct > 0 else 0.0
        score += 10.0 if rsi_now <= (self.rsi_low + self.rsi_high) / 2 else 5.0

        return self._signal(
            score=score,
            reasons=[
                "Tren naik: close > EMA50 > EMA200",
                f"Pullback menyentuh EMA20 dalam {self.pullback_window} bar terakhir",
                f"RSI14 {rsi_now:.1f} berbalik naik dalam rentang {self.rsi_low:.0f}–{self.rsi_high:.0f}",
                "Konfirmasi: close naik dari bar sebelumnya",
            ],
            evidence={
                "close": to_decimal(close),
                "ema20": to_decimal(ema20),
                "ema50": to_decimal(ema50),
                "ema200": to_decimal(ema200),
                "rsi14": to_decimal(rsi_now, 2),
                "rsi14_prev": to_decimal(rsi_prev, 2),
                "atr14": to_decimal(atr),
                "pullback_low": to_decimal(pullback_low),
                "distance_to_ema20_pct": to_decimal(distance_to_ema20_pct, 2),
            },
            entry_low=max(ema20, close - 0.5 * atr),
            entry_high=close,
            structure_stop=min(pullback_low, ema50),
        )
