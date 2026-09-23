"""Scorer: menggabungkan keluaran strategi menjadi confidence 0–100 dan memilih sinyal.

Formula (terdokumentasi, bobot configurable, default 1.0):

    effective_i = clamp(score_i × weight_i, 0, 100)             hanya strategi berstatus SIGNAL
    primary     = strategi dengan effective tertinggi (tie → urutan strategi)
    confluence  = Σ_{i≠primary, effective_i >= confluence_min} confluence_points
                  dibatasi confluence_cap
    penalty     = degraded_penalty jika kualitas data DEGRADED, selain itu 0
    confidence  = clamp(effective_primary + confluence − penalty, 0, 100)

Strategi INACTIVE/INSUFFICIENT_DATA/NO_SETUP menyumbang 0 dan TIDAK mendistribusikan ulang bobot;
data yang hilang tidak pernah menaikkan confidence. Confidence adalah skor engine, bukan
probabilitas untung.

Seleksi: confidence >= threshold; urut confidence ↓, R:R TP1 (bersih) ↓, rata-rata nilai
transaksi 20 hari ↓, simbol A–Z; maksimum ``max_signals``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from config.settings import Settings
from data.providers.base import QualityStatus
from engine.models import SignalCard, StrategyOutcome, StrategyState


@dataclass(frozen=True, slots=True)
class ScorerConfig:
    weights: dict[str, Decimal] = field(default_factory=dict)  # strategy_id → bobot (default 1)
    confluence_min_score: int = 60
    confluence_points: int = 10
    confluence_cap: int = 15
    degraded_penalty: int = 10
    threshold: int = 70
    max_signals: int = 5

    def __post_init__(self) -> None:
        if not 0 <= self.threshold <= 100:
            raise ValueError("threshold harus 0–100")
        if self.max_signals < 1:
            raise ValueError("max_signals minimal 1")
        for k, w in self.weights.items():
            if w < 0:
                raise ValueError(f"bobot {k} negatif")

    @classmethod
    def from_settings(cls, settings: Settings) -> ScorerConfig:
        return cls(
            threshold=settings.scorer_threshold,
            max_signals=settings.scorer_max_signals,
            degraded_penalty=settings.scorer_degraded_penalty,
        )

    def weight(self, strategy_id: str) -> Decimal:
        return self.weights.get(strategy_id, Decimal("1"))

    def as_dict(self) -> dict[str, object]:
        return {
            "weights": {k: str(v) for k, v in sorted(self.weights.items())},
            "confluence_min_score": self.confluence_min_score,
            "confluence_points": self.confluence_points,
            "confluence_cap": self.confluence_cap,
            "degraded_penalty": self.degraded_penalty,
            "threshold": self.threshold,
            "max_signals": self.max_signals,
        }


@dataclass(frozen=True, slots=True)
class ConfidenceResult:
    confidence: int
    primary_strategy: str
    breakdown: dict[str, int]  # primary, confluence, penalty, effective per strategi


def _clamp(value: Decimal | int) -> int:
    return int(max(Decimal("0"), min(Decimal("100"), Decimal(value))))


def compute_confidence(
    outcomes: Sequence[StrategyOutcome], quality: QualityStatus, cfg: ScorerConfig
) -> ConfidenceResult | None:
    signals = [o for o in outcomes if o.state is StrategyState.SIGNAL and o.signal is not None]
    if not signals:
        return None
    effective: list[tuple[int, StrategyOutcome]] = [
        (_clamp(Decimal(o.score) * cfg.weight(o.strategy_id)), o) for o in signals
    ]
    # max() mengembalikan elemen pertama pada nilai sama → tie-break deterministik urutan strategi
    primary_eff, primary = max(effective, key=lambda t: t[0])
    confluence = 0
    for eff, o in effective:
        if o is primary:
            continue
        if eff >= cfg.confluence_min_score:
            confluence += cfg.confluence_points
    confluence = min(confluence, cfg.confluence_cap)
    penalty = cfg.degraded_penalty if quality is QualityStatus.DEGRADED else 0
    confidence = _clamp(primary_eff + confluence - penalty)
    breakdown: dict[str, int] = {
        "primary": primary_eff,
        "confluence": confluence,
        "penalty": penalty,
    }
    for eff, o in effective:
        breakdown[f"effective:{o.strategy_id}"] = eff
    return ConfidenceResult(
        confidence=confidence, primary_strategy=primary.strategy_id, breakdown=breakdown
    )


def rank_cards(cards: Iterable[SignalCard]) -> list[SignalCard]:
    return sorted(
        cards,
        key=lambda c: (-c.confidence, -c.risk.rr_tp1_net, -c.liquidity_avg_value_20d, c.symbol),
    )


def select_cards(cards: Iterable[SignalCard], cfg: ScorerConfig) -> list[SignalCard]:
    eligible = [c for c in cards if c.confidence >= cfg.threshold]
    return rank_cards(eligible)[: cfg.max_signals]
