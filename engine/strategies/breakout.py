"""Breakout v1.

Syarat pada bar lengkap terakhir (candle sinyal, indeks -1):
1. Resistance = max(high) dari ``lookback`` bar SEBELUM candle sinyal (indeks -1-lookback .. -2).
   Candle sinyal tidak ikut membentuk resistance acuan.
2. close[-1] > resistance.
3. volume[-1] > ``volume_multiple`` × rata-rata volume ``lookback`` bar sebelum candle sinyal.
4. Filter tren minimal: close > EMA50.

Entry zone: [max(resistance, close − 1×ATR), close] (retest dekat breakout). Struktur stop:
min(low candle sinyal, resistance − 0.5×ATR).
"""

from __future__ import annotations

from typing import ClassVar

from engine.indicators import RESISTANCE_LOOKBACK, VOLUME_AVG_LENGTH, to_decimal
from engine.models import StrategySignal
from engine.strategies.base import Strategy, StrategyContext, require_bars


class BreakoutStrategy(Strategy):
    id: ClassVar[str] = "breakout"
    version: ClassVar[str] = "1.0.0"

    def __init__(
        self, *, lookback: int = RESISTANCE_LOOKBACK, volume_multiple: float = 1.5
    ) -> None:
        self.lookback = lookback
        self.volume_multiple = volume_multiple

    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None:
        require_bars(ctx, max(self.lookback, VOLUME_AVG_LENGTH) + 1)
        ind = ctx.indicators
        frame = ind.frame
        prior = frame.iloc[-1 - self.lookback : -1]  # tidak termasuk candle sinyal
        resistance = float(prior["high"].max())
        prior_vol = frame["volume"].iloc[-1 - VOLUME_AVG_LENGTH : -1]
        vol_avg_prior = float(prior_vol.mean())
        close, volume, low = ind.last("close"), ind.last("volume"), ind.last("low")
        ema50, atr = ind.last("ema50"), ind.last("atr14")

        if vol_avg_prior <= 0 or resistance <= 0:
            return None
        if not close > resistance:
            return None
        volume_ratio = volume / vol_avg_prior
        if not volume_ratio > self.volume_multiple:
            return None
        if not close > ema50:
            return None

        breakout_margin_pct = (close - resistance) / resistance * 100
        # Skor: dasar 60; +hingga 20 untuk rasio volume (1.5x→0, 3x→20); +hingga 20 untuk
        # margin breakout (0→0, 3%→20). Margin > 6% dianggap sudah terlalu jauh (−10).
        score = 60.0
        score += min(20.0, max(0.0, (volume_ratio - self.volume_multiple) / 1.5 * 20.0))
        score += min(20.0, breakout_margin_pct / 3.0 * 20.0)
        if breakout_margin_pct > 6.0:
            score -= 10.0

        return self._signal(
            score=score,
            reasons=[
                f"Close menembus resistance {self.lookback} bar sebelumnya ({resistance:.0f})",
                f"Volume {volume_ratio:.2f}× rata-rata {VOLUME_AVG_LENGTH} bar sebelumnya",
                "Filter tren: close > EMA50",
            ],
            evidence={
                "close": to_decimal(close),
                "resistance": to_decimal(resistance),
                "volume": int(volume),
                "volume_avg_prior": to_decimal(vol_avg_prior, 0),
                "volume_ratio": to_decimal(volume_ratio, 2),
                "breakout_margin_pct": to_decimal(breakout_margin_pct, 2),
                "ema50": to_decimal(ema50),
                "atr14": to_decimal(atr),
            },
            entry_low=max(resistance, close - atr),
            entry_high=close,
            structure_stop=min(low, resistance - 0.5 * atr),
        )
