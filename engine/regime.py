"""Filter rezim pasar dari indeks acuan (default IHSG ``^JKSE`` via Yahoo).

Tujuan: bot DIAM (tidak menerbitkan setup long baru) ketika pasar secara agregat dalam tren turun.
Definisi eksplisit, dihitung HANYA dari bar indeks lengkap dengan ``session_date`` ≤ sesi evaluasi
(anti-lookahead), dipakai identik oleh live dan backtest:

- ``close > EMA(fast)`` dan ``EMA(fast) > EMA(slow)``  ⇒ ``bullish``
- ``close < EMA(fast)`` dan ``EMA(fast) < EMA(slow)``  ⇒ ``bearish``
- selain itu                                         ⇒ ``neutral``
- Bar indeks untuk sesi evaluasi tidak ada / histori < warmup ⇒ ``unknown``.

Kebijakan: ``allow_new_longs`` benar untuk ``bullish``/``neutral``; ``bearish`` memblokir setup baru;
``unknown`` mengikuti ``policy_on_unknown`` (default ``block`` — fail-closed di produksi/backtest,
dapat diubah ke ``allow`` untuk development). Sinyal yang sudah terbuka tetap dikelola lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

import numpy as np

from config.settings import Settings
from data.providers.base import OHLCVFrame
from engine.indicators import ema, to_decimal


class Regime(StrEnum):
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RegimeConfig:
    enabled: bool = True
    index_symbol: str = "^JKSE"  # dipetakan ke Yahoo apa adanya (bukan saham .JK)
    fast: int = 50
    slow: int = 200
    policy_on_unknown: Literal["block", "allow"] = "block"
    block_neutral: bool = False

    def __post_init__(self) -> None:
        if self.fast < 2 or self.slow <= self.fast:
            raise ValueError("regime: butuh 2 ≤ fast < slow")

    @classmethod
    def from_settings(cls, settings: Settings) -> RegimeConfig:
        return cls(
            enabled=settings.regime_enabled,
            index_symbol=settings.regime_index_symbol,
            fast=settings.regime_fast,
            slow=settings.regime_slow,
            policy_on_unknown=settings.regime_policy_on_unknown,
            block_neutral=settings.regime_block_neutral,
        )

    @property
    def warmup(self) -> int:
        return self.slow + 10

    def as_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "index_symbol": self.index_symbol,
            "fast": self.fast,
            "slow": self.slow,
            "policy_on_unknown": self.policy_on_unknown,
            "block_neutral": self.block_neutral,
        }


@dataclass(frozen=True, slots=True)
class RegimeState:
    regime: Regime
    session_date: date
    index_symbol: str
    close: Decimal | None = None
    ema_fast: Decimal | None = None
    ema_slow: Decimal | None = None
    reason: str = ""

    def allow_new_longs(self, cfg: RegimeConfig) -> bool:
        if not cfg.enabled:
            return True
        if self.regime is Regime.BULLISH:
            return True
        if self.regime is Regime.NEUTRAL:
            return not cfg.block_neutral
        if self.regime is Regime.BEARISH:
            return False
        return cfg.policy_on_unknown == "allow"

    @property
    def summary(self) -> str:
        if self.regime is Regime.UNKNOWN:
            return f"rezim {self.index_symbol}: tidak diketahui ({self.reason})"
        return (
            f"rezim {self.index_symbol}: {self.regime.value} — close {self.close}, "
            f"EMA cepat {self.ema_fast}, EMA lambat {self.ema_slow}"
        )


def compute_regime(
    index_frame: OHLCVFrame | None, session_date: date, cfg: RegimeConfig
) -> RegimeState:
    if not cfg.enabled:
        return RegimeState(Regime.UNKNOWN, session_date, cfg.index_symbol, reason="filter nonaktif")
    if index_frame is None:
        return RegimeState(
            Regime.UNKNOWN, session_date, cfg.index_symbol, reason="data indeks tidak tersedia"
        )
    df = index_frame.frame
    complete = df.loc[df["complete"].astype(bool) & (df["session_date"] <= session_date)]
    if complete.empty or complete["session_date"].iloc[-1] != session_date:
        last = complete["session_date"].iloc[-1] if not complete.empty else None
        return RegimeState(
            Regime.UNKNOWN,
            session_date,
            cfg.index_symbol,
            reason=f"bar indeks untuk {session_date} tidak ada (terakhir {last})",
        )
    if len(complete) < cfg.warmup:
        return RegimeState(
            Regime.UNKNOWN,
            session_date,
            cfg.index_symbol,
            reason=f"histori indeks {len(complete)} bar < warmup {cfg.warmup}",
        )
    close = complete["close"].astype("float64")
    fast = ema(close, cfg.fast).iloc[-1]
    slow = ema(close, cfg.slow).iloc[-1]
    c = float(close.iloc[-1])
    if any(np.isnan(v) for v in (fast, slow)):
        return RegimeState(Regime.UNKNOWN, session_date, cfg.index_symbol, reason="EMA indeks NaN")
    if c > fast > slow:
        regime = Regime.BULLISH
    elif c < fast < slow:
        regime = Regime.BEARISH
    else:
        regime = Regime.NEUTRAL
    return RegimeState(
        regime,
        session_date,
        cfg.index_symbol,
        close=to_decimal(c, 2),
        ema_fast=to_decimal(float(fast), 2),
        ema_slow=to_decimal(float(slow), 2),
    )
