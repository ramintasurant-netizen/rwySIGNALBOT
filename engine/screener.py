"""Screener universe: pengecualian (suspensi/papan pemantauan khusus) dan filter likuiditas.

Universe = watchlist ∪ kandidat tambahan; semua simbol tetap melewati filter ini.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from config.common import canonical_symbol
from config.market_rules import MarketRules
from engine.indicators import IndicatorSet


@dataclass(frozen=True, slots=True)
class LiquidityCheck:
    eligible: bool
    avg_value: Decimal
    avg_volume: Decimal
    lookback_days: int
    value_basis: str
    reason: str = ""


class Screener:
    def __init__(self, rules: MarketRules) -> None:
        self._rules = rules

    def exclusion_reason(self, symbol: str) -> str | None:
        sym = canonical_symbol(symbol)
        if sym in self._rules.exclusions.suspended_symbols:
            return "saham dalam daftar suspensi"
        if sym in self._rules.exclusions.special_monitoring_board_symbols:
            return "saham pada papan pemantauan khusus"
        return None

    def liquidity(self, indicators: IndicatorSet) -> LiquidityCheck:
        liq = self._rules.liquidity
        n = liq.lookback_days
        frame = indicators.frame
        if len(frame) < n:
            return LiquidityCheck(
                False, Decimal("0"), Decimal("0"), n, "", f"histori < {n} bar untuk likuiditas"
            )
        avg_value = Decimal(repr(round(float(frame["value"].iloc[-n:].mean()), 0)))
        avg_volume = Decimal(repr(round(float(frame["volume"].iloc[-n:].mean()), 0)))
        basis = str(frame.attrs.get("value_basis", "approx close×volume"))
        reasons: list[str] = []
        if avg_value < liq.min_avg_daily_value_idr:
            reasons.append(
                f"rata-rata nilai transaksi {avg_value:,.0f} < minimum {liq.min_avg_daily_value_idr:,.0f}"
            )
        if avg_volume < liq.min_avg_daily_volume_shares:
            reasons.append(
                f"rata-rata volume {avg_volume:,.0f} < minimum {liq.min_avg_daily_volume_shares:,}"
            )
        return LiquidityCheck(not reasons, avg_value, avg_volume, n, basis, "; ".join(reasons))

    @staticmethod
    def universe(watchlist: tuple[str, ...], candidates: tuple[str, ...] = ()) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for raw in (*watchlist, *candidates):
            seen.setdefault(canonical_symbol(raw), None)
        return tuple(sorted(seen))
