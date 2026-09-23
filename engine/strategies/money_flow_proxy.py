"""Smart Money PROXY v1 (volume/harga dari Yahoo) — swing.

Bukan data broker/asing. Mengandalkan ``engine.money_flow.compute_money_flow``:
1. CMF20 > ``min_cmf`` dan naik vs ``window`` sesi lalu.
2. OBV masuk netto ≥ ``min_obv_days`` "hari volume" selama jendela.
3. Hari akumulasi ≥ ``min_acc_days`` dan hari akumulasi > hari distribusi.
4. Rasio volume naik/turun ≥ ``min_updown``.
5. Filter tren: close > EMA50. Bonus quiet accumulation (harga belum lari) +10; MFI < 70 (belum jenuh) +5.

Skor: 55 dasar + CMF (0,10→0, 0,30→+15) + OBV (2→0, 6 hari→+15) + acc_days (3→0, 7→+10) + bonus.
Entry zone [max(EMA20, close − 0,5×ATR), close]; struktur stop = low terendah jendela.
"""

from __future__ import annotations

from typing import ClassVar

from engine.indicators import to_decimal
from engine.models import StrategySignal
from engine.money_flow import compute_money_flow
from engine.strategies.base import Strategy, StrategyContext, require_bars


class MoneyFlowProxyStrategy(Strategy):
    id: ClassVar[str] = "money_flow_proxy"
    version: ClassVar[str] = "1.0.0"

    def __init__(
        self,
        *,
        window: int = 10,
        min_cmf: float = 0.10,
        min_obv_days: float = 2.0,
        min_acc_days: int = 3,
        min_updown: float = 1.3,
        quiet_pct: float = 5.0,
    ) -> None:
        self.window = window
        self.min_cmf = min_cmf
        self.min_obv_days = min_obv_days
        self.min_acc_days = min_acc_days
        self.min_updown = min_updown
        self.quiet_pct = quiet_pct

    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None:
        require_bars(ctx, self.window + 2)
        mf = compute_money_flow(
            ctx.indicators, ctx.symbol, window=self.window, quiet_pct=self.quiet_pct
        )
        if mf is None:
            return None
        cmf, cmf_prev = float(mf.cmf20), float(mf.cmf_prev)
        if not (cmf > self.min_cmf and cmf > cmf_prev):
            return None
        if float(mf.obv_slope_days) < self.min_obv_days:
            return None
        if mf.acc_days < self.min_acc_days or mf.acc_days <= mf.dist_days:
            return None
        if float(mf.updown_ratio) < self.min_updown:
            return None
        ind = ctx.indicators
        close, ema20, ema50, atr = (
            ind.last("close"),
            ind.last("ema20"),
            ind.last("ema50"),
            ind.last("atr14"),
        )
        if not close > ema50:
            return None

        score = 55.0
        score += min(15.0, max(0.0, (cmf - self.min_cmf) / 0.20 * 15.0))
        score += min(15.0, max(0.0, (float(mf.obv_slope_days) - self.min_obv_days) / 4.0 * 15.0))
        score += min(10.0, max(0.0, (mf.acc_days - self.min_acc_days) / 4.0 * 10.0))
        if mf.quiet:
            score += 10.0
        if float(mf.mfi14) < 70:
            score += 5.0

        reasons = [
            f"Proxy smart money (volume Yahoo): CMF20 {cmf:.2f} naik dari {cmf_prev:.2f}",
            f"OBV masuk netto ≈ {float(mf.obv_slope_days):.1f} hari volume dalam {self.window} sesi; hari akumulasi {mf.acc_days} vs distribusi {mf.dist_days}",
            f"Volume naik/turun {float(mf.updown_ratio):.2f}×",
            "Harga masih tenang (belum lari)"
            if mf.quiet
            else f"Harga sudah bergerak {float(mf.price_change_pct):.1f}%",
            "Filter tren: close > EMA50",
        ]
        lows = float(ind.frame["low"].iloc[-self.window :].min())
        return self._signal(
            score=score,
            reasons=reasons,
            evidence={
                "close": to_decimal(close),
                "ema20": to_decimal(ema20),
                "ema50": to_decimal(ema50),
                "atr14": to_decimal(atr),
                "cmf20": mf.cmf20,
                "cmf20_prev": mf.cmf_prev,
                "mfi14": mf.mfi14,
                "obv_slope_days": mf.obv_slope_days,
                "acc_days": mf.acc_days,
                "dist_days": mf.dist_days,
                "updown_ratio": mf.updown_ratio,
                "price_change_pct": mf.price_change_pct,
                "quiet_accumulation": mf.quiet,
                "money_flow_score": mf.score,
                "data_basis": "proxy volume/harga (bukan data broker)",
            },
            entry_low=max(ema20, close - 0.5 * atr),
            entry_high=close,
            structure_stop=lows,
        )
