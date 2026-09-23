"""ReportSnapshot: kontrak tunggal antara engine → narator → formatter → notifier.

Semua angka trading berasal dari ``EngineResult`` dan disalin apa adanya (Decimal → str agar
JSON stabil dan bebas float). Formatter TIDAK menghitung ulang apa pun dari snapshot ini.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from data.providers.base import QualityStatus
from data.providers.global_macro import GlobalContext
from data.providers.news import NewsFetchResult
from engine.models import EngineResult, SignalCard

DISCLAIMER = (
    "Bukan ajakan jual/beli. Analisis bersifat informasional. "
    "Keputusan dan risiko sepenuhnya milik Anda."
)


class ReportType(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"

    @property
    def title(self) -> str:
        return "PRE-MARKET BRIEF" if self is ReportType.MORNING else "PRE-CLOSE SIGNAL"


class SnapshotOrigin(StrEnum):
    LIVE = "live"
    DRY_RUN = "dry_run"
    FIXTURE = "fixture"
    BACKTEST = "backtest"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BlockedInfo(_Model):
    symbol: str
    stage: str
    reason: str


class DataQualitySummary(_Model):
    symbols_total: int
    symbols_evaluated: int
    blocked: tuple[BlockedInfo, ...] = ()
    providers_used: tuple[str, ...] = ()
    quality_levels: dict[str, int] = Field(default_factory=dict)  # "ok"/"degraded" → jumlah
    rules_label: str
    calendar_label: str
    notes: tuple[str, ...] = ()


class MacroItemSnapshot(_Model):
    id: str
    label: str
    unit: str
    status: str  # ok | unavailable | failed
    verified: bool
    last: str | None = None
    change_pct: str | None = None
    as_of: datetime | None = None
    reason: str = ""


class GlobalContextSnapshot(_Model):
    status: str  # ok | partial | unavailable
    fetched_at: datetime | None
    items: tuple[MacroItemSnapshot, ...] = ()
    reason: str = ""


class NewsItemSnapshot(_Model):
    source: str
    title: str
    url: str
    published_at: datetime | None


class NewsSnapshot(_Model):
    status: str  # ok | partial | unavailable | not_configured
    items: tuple[NewsItemSnapshot, ...] = ()
    errors: dict[str, str] = Field(default_factory=dict)


class SizingSnapshot(_Model):
    capital_example: str
    risk_pct: str
    risk_amount: str
    lot_size: int
    lots: int
    shares: int
    notional: str
    note: str = ""


class SignalCardSnapshot(_Model):
    symbol: str
    strategy: str
    strategy_version: str
    confidence: int
    score_breakdown: dict[str, int]
    entry_low: str
    entry_high: str
    stop_loss: str
    tp1: str
    tp2: str
    tp3: str
    r_value: str
    rr_tp1_gross: str
    rr_tp1_net: str
    ara: str
    arb: str
    atr: str
    sizing: SizingSnapshot | None
    reasons: tuple[str, ...]
    evidence: dict[str, str]
    risk_notes: tuple[str, ...]
    provider: str
    bar_time: datetime
    session_date: date
    quality: str
    entry_valid_sessions: int


class ActiveSignalSnapshot(_Model):
    symbol: str
    strategy: str
    status: str
    published_session: date
    entry_low: str
    entry_high: str
    stop_loss: str
    tp1: str
    filled_price: str | None = None
    last_price: str | None = None
    last_price_time: datetime | None = None


class MoneyFlowSnapshot(_Model):
    """Proxy money flow dari harga & volume (bukan data broker/asing)."""

    symbol: str
    label: str  # akumulasi | distribusi | netral
    score: int
    cmf20: str
    obv_slope_days: str
    acc_days: int
    dist_days: int
    updown_ratio: str
    price_change_pct: str
    quiet: bool


class SignalUpdateSnapshot(_Model):
    symbol: str
    previous_status: str
    new_status: str
    trigger_price: str | None
    trigger_session: date | None
    note: str = ""
    pnl_r: str | None = None


class ReportSnapshot(_Model):
    schema_version: int = 1
    report_type: ReportType
    origin: SnapshotOrigin
    trading_date: date
    data_session_date: date
    generated_at: datetime
    engine_version: str
    strategy_versions: dict[str, str]
    config_hash: str
    data_quality: DataQualitySummary
    global_context: GlobalContextSnapshot
    news: NewsSnapshot
    signals: tuple[SignalCardSnapshot, ...]
    active_signals: tuple[ActiveSignalSnapshot, ...] = ()
    active_updates: tuple[SignalUpdateSnapshot, ...] = ()
    money_flow: tuple[MoneyFlowSnapshot, ...] = ()  # diurutkan skor menurun; proxy volume
    narrative: str | None = None
    market_bias: str | None = (
        None  # dari filter rezim terdefinisi (bullish/neutral/bearish); None = dihilangkan
    )
    regime_detail: str | None = None
    disclaimer: str = DISCLAIMER
    engine_notes: tuple[str, ...] = ()

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> ReportSnapshot:
        return cls.model_validate_json(raw)


# ----------------------------------------------------------------------------- pembangun


def _s(value: Decimal | int | float | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return (
            format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")
        )
    return str(value)


def _evidence_to_str(evidence: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in evidence.items():
        if isinstance(v, dict):
            out[k] = ", ".join(f"{ik}={iv}" for ik, iv in v.items())
        else:
            out[k] = _s(v) if isinstance(v, Decimal) else str(v)
    return out


def card_to_snapshot(card: SignalCard, *, entry_valid_sessions: int) -> SignalCardSnapshot:
    r = card.risk
    sizing = None
    if r.sizing is not None:
        s = r.sizing
        sizing = SizingSnapshot(
            capital_example=_s(s.capital_example) or "0",
            risk_pct=_s(s.risk_pct) or "0",
            risk_amount=_s(s.risk_amount) or "0",
            lot_size=s.lot_size,
            lots=s.lots,
            shares=s.shares,
            notional=_s(s.notional) or "0",
            note=s.note,
        )
    return SignalCardSnapshot(
        symbol=card.symbol,
        strategy=card.primary_strategy,
        strategy_version=card.strategy_versions.get(card.primary_strategy, "?"),
        confidence=card.confidence,
        score_breakdown=dict(card.score_breakdown),
        entry_low=_s(r.entry_low) or "0",
        entry_high=_s(r.entry_high) or "0",
        stop_loss=_s(r.stop_loss) or "0",
        tp1=_s(r.tp1) or "0",
        tp2=_s(r.tp2) or "0",
        tp3=_s(r.tp3) or "0",
        r_value=_s(r.r_value) or "0",
        rr_tp1_gross=_s(r.rr_tp1_gross) or "0",
        rr_tp1_net=_s(r.rr_tp1_net) or "0",
        ara=_s(r.ara) or "0",
        arb=_s(r.arb) or "0",
        atr=_s(r.atr) or "0",
        sizing=sizing,
        reasons=card.reasons,
        evidence=_evidence_to_str(card.evidence),
        risk_notes=r.notes,
        provider=card.data.provider,
        bar_time=card.data.bar_time,
        session_date=card.data.session_date,
        quality=card.data.quality.value,
        entry_valid_sessions=entry_valid_sessions,
    )


def global_context_to_snapshot(
    ctx: GlobalContext | None, reason: str = ""
) -> GlobalContextSnapshot:
    if ctx is None:
        return GlobalContextSnapshot(
            status="unavailable", fetched_at=None, reason=reason or "tidak diambil"
        )
    items = tuple(
        MacroItemSnapshot(
            id=i.id,
            label=i.label,
            unit=i.unit,
            status=i.status,
            verified=i.verified,
            last=_s(i.last),
            change_pct=_s(i.change_pct),
            as_of=i.as_of,
            reason=i.reason,
        )
        for i in ctx.items
    )
    ok = sum(1 for i in items if i.status == "ok")
    status = "ok" if ok == len(items) and items else "partial" if ok else "unavailable"
    return GlobalContextSnapshot(status=status, fetched_at=ctx.fetched_at, items=items)


def news_to_snapshot(result: NewsFetchResult | None, *, max_items: int = 8) -> NewsSnapshot:
    if result is None:
        return NewsSnapshot(status="unavailable")
    if not result.sources_tried:
        return NewsSnapshot(status="not_configured")
    items = tuple(
        NewsItemSnapshot(source=i.source, title=i.title, url=i.url, published_at=i.published_at)
        for i in result.items[:max_items]
    )
    if result.all_failed:
        status = "unavailable"
    elif result.errors:
        status = "partial"
    else:
        status = "ok"
    return NewsSnapshot(status=status, items=items, errors=dict(result.errors))


def build_snapshot(
    *,
    report_type: ReportType,
    origin: SnapshotOrigin,
    trading_date: date,
    generated_at: datetime,
    engine_result: EngineResult,
    strategy_versions: dict[str, str],
    quality_by_symbol: dict[str, QualityStatus],
    providers_used: tuple[str, ...],
    data_blocked: tuple[BlockedInfo, ...],
    rules_label: str,
    calendar_label: str,
    global_context: GlobalContextSnapshot,
    news: NewsSnapshot,
    entry_valid_sessions: int,
    active_signals: tuple[ActiveSignalSnapshot, ...] = (),
    active_updates: tuple[SignalUpdateSnapshot, ...] = (),
    money_flow: tuple[MoneyFlowSnapshot, ...] = (),
    data_notes: tuple[str, ...] = (),
) -> ReportSnapshot:
    levels: dict[str, int] = {}
    for q in quality_by_symbol.values():
        levels[q.value] = levels.get(q.value, 0) + 1
    engine_blocked = tuple(
        BlockedInfo(symbol=b.symbol, stage=b.stage, reason=b.reason) for b in engine_result.blocked
    )
    return ReportSnapshot(
        report_type=report_type,
        origin=origin,
        trading_date=trading_date,
        data_session_date=engine_result.session_date,
        generated_at=generated_at,
        engine_version=engine_result.engine_version,
        strategy_versions=strategy_versions,
        config_hash=engine_result.config_hash,
        data_quality=DataQualitySummary(
            symbols_total=len(engine_result.universe) + len(data_blocked),
            symbols_evaluated=engine_result.symbols_evaluated,
            blocked=(*data_blocked, *engine_blocked),
            providers_used=providers_used,
            quality_levels=levels,
            rules_label=rules_label,
            calendar_label=calendar_label,
            notes=data_notes,
        ),
        global_context=global_context,
        news=news,
        signals=tuple(
            card_to_snapshot(c, entry_valid_sessions=entry_valid_sessions)
            for c in engine_result.cards
        ),
        active_signals=active_signals,
        active_updates=active_updates,
        money_flow=money_flow,
        engine_notes=engine_result.notes,
        market_bias=engine_result.regime if engine_result.regime not in (None, "unknown") else None,
    )
