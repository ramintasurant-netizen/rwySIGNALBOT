"""Orkestrasi engine untuk satu sesi evaluasi: screener → indikator → strategi → scorer → risk.

Batas anti-lookahead ada DI SINI: frame dipotong ke bar lengkap dengan
``session_date <= sesi evaluasi`` sebelum strategi melihatnya. Backtest memakai kelas yang sama.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from config.market_rules import MarketRules
from data.providers.base import BrokerSummary, ForeignFlow, OHLCVFrame, PriceBasis, QualityStatus
from engine import ENGINE_VERSION
from engine.indicators import WARMUP_BARS, InsufficientHistoryError, compute_indicators, to_decimal
from engine.models import (
    BlockedSymbol,
    DataProvenance,
    EngineResult,
    SignalCard,
    StrategyOutcome,
    SymbolEvaluation,
)
from engine.risk import RiskConfig, RiskRejected, build_risk_plan
from engine.scorer import ScorerConfig, compute_confidence, select_cards
from engine.screener import Screener
from engine.strategies import DEFAULT_STRATEGIES, Strategy, StrategyContext

USABLE_QUALITY = (QualityStatus.OK, QualityStatus.DEGRADED)


@dataclass(frozen=True, slots=True)
class SymbolInput:
    frame: OHLCVFrame
    quality: QualityStatus
    foreign_flows: tuple[ForeignFlow, ...] | None = None
    foreign_flow_reason: str = ""
    broker_summary: BrokerSummary | None = None
    broker_summary_reason: str = ""
    broker_summaries: tuple[BrokerSummary, ...] | None = None


class SignalEngine:
    def __init__(
        self,
        rules: MarketRules,
        *,
        risk: RiskConfig | None = None,
        scorer: ScorerConfig | None = None,
        strategies: Sequence[Strategy] | None = None,
        warmup: int = WARMUP_BARS,
        with_sizing: bool = True,
    ) -> None:
        self.rules = rules
        self.risk = risk or RiskConfig()
        self.scorer = scorer or ScorerConfig()
        self.strategies: tuple[Strategy, ...] = (
            tuple(strategies)
            if strategies is not None
            else tuple(cls() for cls in DEFAULT_STRATEGIES)
        )
        self.warmup = warmup
        self.with_sizing = with_sizing
        self.screener = Screener(rules)

    # ------------------------------------------------------------------ identitas konfigurasi
    @property
    def strategy_versions(self) -> dict[str, str]:
        return {s.id: s.version for s in self.strategies}

    def config_fingerprint(self) -> dict[str, object]:
        return {
            "engine_version": ENGINE_VERSION,
            "warmup": self.warmup,
            "rules": self.rules.model_dump(mode="json"),
            "risk": self.risk.as_dict(),
            "scorer": self.scorer.as_dict(),
            "strategies": [
                {
                    "id": s.id,
                    "version": s.version,
                    "params": {k: v for k, v in sorted(vars(s).items()) if not k.startswith("_")},
                }
                for s in self.strategies
            ],
        }

    @property
    def config_hash(self) -> str:
        payload = json.dumps(
            self.config_fingerprint(), sort_keys=True, default=str, ensure_ascii=False
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------------ evaluasi per simbol
    def evaluate_symbol(
        self, symbol: str, data: SymbolInput, session_date: date
    ) -> SymbolEvaluation:
        def blocked(stage: str, reason: str) -> SymbolEvaluation:
            return SymbolEvaluation(symbol, (), None, None, BlockedSymbol(symbol, stage, reason))

        excl = self.screener.exclusion_reason(symbol)
        if excl:
            return blocked("screener", excl)
        if data.quality not in USABLE_QUALITY:
            return blocked("data", f"kualitas data {data.quality.value}")
        frame = data.frame
        if frame.price_basis is not PriceBasis.RAW:
            return blocked("data", "engine membutuhkan seri harga raw untuk aturan perdagangan")
        if frame.symbol != symbol:
            return blocked("data", f"frame untuk {frame.symbol}, bukan {symbol}")

        df = frame.frame
        complete = df.loc[df["complete"].astype(bool) & (df["session_date"] <= session_date)]
        if complete.empty:
            return blocked("data", "tidak ada bar lengkap sampai sesi evaluasi")
        last_session = complete["session_date"].iloc[-1]
        if last_session != session_date:
            return blocked(
                "data", f"sesi lengkap terakhir {last_session} ≠ sesi evaluasi {session_date}"
            )

        try:
            indicators = compute_indicators(complete, warmup=self.warmup)
        except InsufficientHistoryError as exc:
            return blocked("indicators", str(exc))

        liquidity = self.screener.liquidity(indicators)
        if not liquidity.eligible:
            return blocked("screener", liquidity.reason)

        ctx = StrategyContext(
            symbol=symbol,
            session_date=session_date,
            indicators=indicators,
            quality=data.quality,
            foreign_flows=data.foreign_flows,
            foreign_flow_reason=data.foreign_flow_reason,
            broker_summary=data.broker_summary,
            broker_summary_reason=data.broker_summary_reason,
            broker_summaries=data.broker_summaries,
        )
        outcomes: tuple[StrategyOutcome, ...] = tuple(s.outcome(ctx) for s in self.strategies)
        conf = compute_confidence(outcomes, data.quality, self.scorer)
        if conf is None:
            return SymbolEvaluation(symbol, outcomes, None, None, None)

        primary = next(o for o in outcomes if o.strategy_id == conf.primary_strategy)
        assert primary.signal is not None
        atr = to_decimal(indicators.last("atr14"))
        reference_price = to_decimal(indicators.last("close"))
        try:
            plan = build_risk_plan(
                primary.signal,
                atr=atr,
                reference_price=reference_price,
                rules=self.rules,
                cfg=self.risk,
                with_sizing=self.with_sizing,
            )
        except RiskRejected as exc:
            return SymbolEvaluation(
                symbol, outcomes, conf.confidence, None, BlockedSymbol(symbol, "risk", exc.reason)
            )

        reasons = list(primary.signal.reasons)
        for o in outcomes:
            if (
                o is not primary
                and o.signal is not None
                and conf.breakdown.get(f"effective:{o.strategy_id}", 0)
                >= self.scorer.confluence_min_score
            ):
                reasons.append(f"Konfluens {o.strategy_id}: {o.signal.reasons[0]}")
        evidence = dict(primary.signal.evidence)
        evidence["liquidity_value_basis"] = liquidity.value_basis
        card = SignalCard(
            symbol=symbol,
            primary_strategy=conf.primary_strategy,
            strategy_versions=self.strategy_versions,
            confidence=conf.confidence,
            score_breakdown=conf.breakdown,
            risk=plan,
            reasons=tuple(reasons),
            evidence=evidence,
            data=DataProvenance(
                provider=frame.provider,
                bar_time=complete.index[-1].to_pydatetime(),
                session_date=last_session,
                fetched_at=frame.fetched_at,
                quality=data.quality,
                bars_used=len(complete),
            ),
            liquidity_avg_value_20d=liquidity.avg_value,
        )
        return SymbolEvaluation(symbol, outcomes, conf.confidence, card, None)

    # ------------------------------------------------------------------ satu sesi penuh
    def run(self, session_date: date, universe: dict[str, SymbolInput]) -> EngineResult:
        evaluations = tuple(
            self.evaluate_symbol(symbol, universe[symbol], session_date)
            for symbol in sorted(universe)
        )
        cards = select_cards((e.card for e in evaluations if e.card is not None), self.scorer)
        blocked = tuple(e.blocked for e in evaluations if e.blocked is not None)
        notes: list[str] = []
        below = [
            e
            for e in evaluations
            if e.card is not None
            and e.confidence is not None
            and e.confidence < self.scorer.threshold
        ]
        if below:
            notes.append(f"{len(below)} setup di bawah threshold {self.scorer.threshold}")
        if not cards and any(e.blocked is None for e in evaluations):
            notes.append("data valid tetapi tidak ada setup layak")
        return EngineResult(
            session_date=session_date,
            engine_version=ENGINE_VERSION,
            config_hash=self.config_hash,
            cards=tuple(cards),
            evaluations=evaluations,
            blocked=blocked,
            universe=tuple(sorted(universe)),
            notes=tuple(notes),
        )
