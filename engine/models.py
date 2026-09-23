"""Tipe data engine. Harga selalu ``Decimal``; skor bilangan bulat 0–100."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from data.providers.base import QualityStatus


class Direction(StrEnum):
    LONG = "long"


class StrategyState(StrEnum):
    SIGNAL = "signal"  # setup terdeteksi
    NO_SETUP = "no_setup"  # data cukup, syarat tidak terpenuhi
    INACTIVE = "inactive"  # data yang diperlukan tidak tersedia/tidak didukung
    INSUFFICIENT_DATA = "insufficient_data"  # histori kurang dari warmup


@dataclass(frozen=True, slots=True)
class StrategySignal:
    """Keluaran satu strategi untuk satu simbol pada satu sesi evaluasi."""

    strategy_id: str
    strategy_version: str
    score: int  # 0–100
    reasons: tuple[str, ...]
    evidence: dict[str, Any]  # indikator → nilai (Decimal/int/str), untuk audit
    data_requirements: tuple[str, ...]
    entry_low_hint: Decimal
    entry_high_hint: Decimal
    structure_stop_hint: Decimal | None
    direction: Direction = Direction.LONG

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 100:
            raise ValueError(f"skor strategi harus 0–100, diterima {self.score}")
        if self.entry_low_hint > self.entry_high_hint:
            raise ValueError("entry_low_hint > entry_high_hint")


@dataclass(frozen=True, slots=True)
class StrategyOutcome:
    strategy_id: str
    strategy_version: str
    state: StrategyState
    signal: StrategySignal | None = None
    reason: str = ""

    @property
    def score(self) -> int:
        return self.signal.score if self.signal is not None else 0


@dataclass(frozen=True, slots=True)
class Sizing:
    capital_example: Decimal
    risk_pct: Decimal
    risk_amount: Decimal
    lot_size: int
    lots: int
    shares: int
    notional: Decimal
    risk_per_share: Decimal
    note: str = ""


@dataclass(frozen=True, slots=True)
class RiskPlan:
    entry_low: Decimal
    entry_high: Decimal
    stop_loss: Decimal
    tp1: Decimal
    tp2: Decimal
    tp3: Decimal
    r_value: Decimal  # entry_high - stop_loss (entry paling konservatif)
    rr_tp1_gross: Decimal
    rr_tp1_net: Decimal  # setelah biaya beli/jual
    reference_price: Decimal  # dasar ARA/ARB (close sesi lengkap terakhir)
    ara: Decimal
    arb: Decimal
    atr: Decimal
    sizing: Sizing | None
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DataProvenance:
    provider: str
    bar_time: datetime
    session_date: date
    fetched_at: datetime
    quality: QualityStatus
    bars_used: int


@dataclass(frozen=True, slots=True)
class SignalCard:
    symbol: str
    primary_strategy: str
    strategy_versions: dict[str, str]
    confidence: int
    score_breakdown: dict[str, int]
    risk: RiskPlan
    reasons: tuple[str, ...]
    evidence: dict[str, Any]
    data: DataProvenance
    liquidity_avg_value_20d: Decimal
    direction: Direction = Direction.LONG


@dataclass(frozen=True, slots=True)
class BlockedSymbol:
    symbol: str
    stage: str  # screener | data | indicators | risk | scorer
    reason: str


@dataclass(frozen=True, slots=True)
class SymbolEvaluation:
    symbol: str
    outcomes: tuple[StrategyOutcome, ...]
    confidence: int | None
    card: SignalCard | None
    blocked: BlockedSymbol | None
    money_flow: Any | None = None  # engine.money_flow.MoneyFlowStats (proxy volume), bila dihitung


@dataclass(frozen=True, slots=True)
class EngineResult:
    session_date: date
    engine_version: str
    config_hash: str
    cards: tuple[SignalCard, ...]
    evaluations: tuple[SymbolEvaluation, ...]
    blocked: tuple[BlockedSymbol, ...]
    universe: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)
    regime: str | None = None  # bullish | neutral | bearish | unknown | None (tanpa filter)

    @property
    def symbols_evaluated(self) -> int:
        return sum(1 for e in self.evaluations if e.blocked is None)
