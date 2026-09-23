from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import ClassVar

from data.providers.base import BrokerSummary, ForeignFlow, QualityStatus
from engine.indicators import IndicatorSet, to_decimal
from engine.models import StrategyOutcome, StrategySignal, StrategyState


class InsufficientData(Exception):  # noqa: N818 - sinyal alur, bukan error program
    pass


class InactiveStrategy(Exception):  # noqa: N818
    pass


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Semua yang boleh dilihat strategi. ``indicators.frame`` HANYA berisi bar lengkap
    sampai ``session_date`` — pemanggil (pipeline/backtest) bertanggung jawab memotongnya."""

    symbol: str
    session_date: date
    indicators: IndicatorSet
    quality: QualityStatus
    foreign_flows: tuple[ForeignFlow, ...] | None = None  # None = tidak tersedia/tidak didukung
    foreign_flow_reason: str = ""
    broker_summary: BrokerSummary | None = None
    broker_summary_reason: str = ""
    # Histori broker summary beberapa sesi (terurut naik); None = tidak tersedia/tidak didukung.
    broker_summaries: tuple[BrokerSummary, ...] | None = None
    extra: dict[str, object] = field(default_factory=dict)


class Strategy(ABC):
    id: ClassVar[str]
    version: ClassVar[str]
    data_requirements: ClassVar[tuple[str, ...]] = ("ohlcv_daily_complete",)
    supports_incomplete_bar: ClassVar[bool] = False

    def outcome(self, ctx: StrategyContext) -> StrategyOutcome:
        try:
            signal = self.evaluate(ctx)
        except InsufficientData as exc:
            return StrategyOutcome(
                self.id, self.version, StrategyState.INSUFFICIENT_DATA, reason=str(exc)
            )
        except InactiveStrategy as exc:
            return StrategyOutcome(self.id, self.version, StrategyState.INACTIVE, reason=str(exc))
        if signal is None:
            return StrategyOutcome(self.id, self.version, StrategyState.NO_SETUP)
        return StrategyOutcome(self.id, self.version, StrategyState.SIGNAL, signal=signal)

    @abstractmethod
    def evaluate(self, ctx: StrategyContext) -> StrategySignal | None: ...

    def _signal(
        self,
        *,
        score: float,
        reasons: list[str],
        evidence: dict[str, object],
        entry_low: float,
        entry_high: float,
        structure_stop: float | None,
    ) -> StrategySignal:
        return StrategySignal(
            strategy_id=self.id,
            strategy_version=self.version,
            score=max(0, min(100, int(round(score)))),
            reasons=tuple(reasons),
            evidence=evidence,
            data_requirements=self.data_requirements,
            entry_low_hint=to_decimal(min(entry_low, entry_high)),
            entry_high_hint=to_decimal(entry_high),
            structure_stop_hint=to_decimal(structure_stop) if structure_stop is not None else None,
        )


def require_bars(ctx: StrategyContext, n: int) -> None:
    usable = len(ctx.indicators.usable)
    if usable < n:
        raise InsufficientData(f"butuh {n} bar matang, tersedia {usable}")
