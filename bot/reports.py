"""ReportService: orkestrasi job pagi/sore — preflight, klaim atomik, data, engine, lifecycle,
snapshot immutable, format, pengiriman dengan status jujur, ekspor dry-run, alert.

Dipakai oleh scheduler, ``/runnow`` admin, dan ``main.py dryrun``. Semua jalur melewati gate,
kalender, pause, kualitas data, dan dedup yang sama.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from loguru import logger

from bot.alerts import AlertSink
from bot.formatter import format_report
from bot.gates import GateResult, evaluate_gates
from config.market_rules import MarketRules
from config.settings import Settings
from config.trading_calendar import CalendarCoverageError, TradingCalendar
from core.redaction import redact_exception
from core.snapshot import (
    ActiveSignalSnapshot,
    BlockedInfo,
    GlobalContextSnapshot,
    NewsSnapshot,
    ReportSnapshot,
    ReportType,
    SignalUpdateSnapshot,
    SnapshotOrigin,
    build_snapshot,
    global_context_to_snapshot,
    news_to_snapshot,
)
from core.timeutil import to_wib, utc_now
from data.aggregator import AggregatedOHLCV, MarketDataAggregator
from data.providers.base import OHLCVFrame, QualityStatus, Timeframe
from data.providers.global_macro import GlobalMacroProvider
from data.providers.news import NewsProvider
from engine import ENGINE_VERSION
from engine.lifecycle import Bar, LifecycleConfig, SignalState, SignalStatus, step
from engine.models import EngineResult
from engine.pipeline import SignalEngine, SymbolInput
from notifications.base import DeliveryStatus, Notifier, TargetRejectedError, TargetVerification
from storage.models import JobRun, Signal
from storage.repository import DeliveryPart, Repository

AFTERNOON_NOTE = (
    "Laporan sore: strategi berbasis close harian tidak dijalankan pada bar yang belum lengkap; "
    "hanya update status sinyal dan validasi ulang setup pending."
)


@dataclass(frozen=True, slots=True)
class DeliverySummary:
    planned: int = 0
    sent: int = 0
    failed: int = 0
    unknown: int = 0
    skipped: int = 0

    @property
    def as_text(self) -> str:
        return (
            f"terkirim {self.sent}, gagal {self.failed}, unknown {self.unknown}, "
            f"dilewati {self.skipped} dari {self.planned}"
        )


@dataclass(frozen=True, slots=True)
class JobOutcome:
    status: str  # completed | blocked | failed | skipped
    report_type: ReportType
    trading_date: date
    origin: SnapshotOrigin | None = None
    job_id: int | None = None
    reason: str = ""
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    snapshot: ReportSnapshot | None = None
    parts: tuple[str, ...] = ()
    delivery: DeliverySummary | None = None
    export_path: Path | None = None
    live_sent: bool = False


@dataclass
class ReportDependencies:
    settings: Settings
    rules: MarketRules
    calendar: TradingCalendar
    repo: Repository
    aggregator: MarketDataAggregator
    engine: SignalEngine
    alerts: AlertSink
    notifier: Notifier | None = None
    macro: GlobalMacroProvider | None = None
    news: NewsProvider | None = None
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    clock: Callable[[], datetime] = utc_now
    export_dir: Path | None = None
    job_timeout_seconds: float = 600.0
    fetch_concurrency: int = 8


class ReportService:
    def __init__(self, deps: ReportDependencies) -> None:
        self.d = deps
        self._running = asyncio.Lock()

    # ------------------------------------------------------------------ util
    @property
    def origin(self) -> SnapshotOrigin:
        return SnapshotOrigin.LIVE if self.d.settings.app_mode == "live" else SnapshotOrigin.DRY_RUN

    def _now(self) -> datetime:
        return self.d.clock()

    async def _verify_targets(self) -> dict[int, TargetVerification] | None:
        if self.d.notifier is None or not self.d.settings.live_send_allowed:
            return None
        out: dict[int, TargetVerification] = {}
        for target in self.d.settings.signal_targets:
            out[target.chat_id] = await self.d.notifier.verify_target(target)
        return out

    # ------------------------------------------------------------------ jalur utama
    async def run(
        self, report_type: ReportType, *, trigger: str = "schedule", now: datetime | None = None
    ) -> JobOutcome:
        async with self._running:
            started = now or self._now()
            try:
                return await asyncio.wait_for(
                    self._run(report_type, trigger=trigger, now=started),
                    timeout=self.d.job_timeout_seconds,
                )
            except TimeoutError:
                trading_date = to_wib(started).date()
                await self.d.alerts.alert(
                    "job_timeout", f"job {report_type.value} {trading_date} melewati batas waktu"
                )
                return JobOutcome("failed", report_type, trading_date, reason="timeout job")

    async def _run(self, report_type: ReportType, *, trigger: str, now: datetime) -> JobOutcome:
        d = self.d
        trading_date = to_wib(now).date()
        origin = self.origin
        paused = await d.repo.is_paused()
        verifications = await self._verify_targets()
        gates = evaluate_gates(
            d.settings,
            d.rules,
            d.calendar,
            trading_date,
            paused=paused,
            backtest_gate=await d.repo.backtest_gate(),
            strategy_versions=d.engine.strategy_versions,
            config_hash=d.engine.config_hash,
            target_verifications=verifications,
        )
        for w in gates.warnings:
            logger.warning("gate warning: {}", w)
        if not gates.job_allowed:
            logger.info(
                "job {} {} dilewati: {}", report_type.value, trading_date, gates.hard_blockers
            )
            if trigger == "manual":
                await d.alerts.alert(
                    "job_skipped",
                    f"/runnow {report_type.value} dilewati: {'; '.join(gates.hard_blockers)}",
                    force=True,
                )
            return JobOutcome(
                "skipped",
                report_type,
                trading_date,
                origin,
                reason="; ".join(gates.hard_blockers),
                blockers=gates.hard_blockers,
                warnings=gates.warnings,
            )

        claim = await d.repo.claim_job(
            job_type=report_type.value,
            trading_date=trading_date,
            origin=origin.value,
            engine_version=ENGINE_VERSION,
            config_hash=d.engine.config_hash,
            trigger=trigger,
        )
        if not claim.claimed or claim.job is None:
            logger.info(
                "job {} {} tidak diklaim: {}", report_type.value, trading_date, claim.reason
            )
            return JobOutcome(
                "skipped",
                report_type,
                trading_date,
                origin,
                reason=claim.reason,
                warnings=gates.warnings,
            )
        job = claim.job
        await d.repo.mark_job(job.id, "running")
        try:
            if report_type is ReportType.MORNING:
                return await self._morning(job, trading_date, gates, now)
            return await self._afternoon(job, trading_date, gates, now)
        except Exception as exc:  # noqa: BLE001 - job tidak boleh menjatuhkan proses
            msg = redact_exception(exc)
            logger.exception("job {} gagal", job.id)
            await d.repo.mark_job(job.id, "failed", error=msg)
            await d.alerts.alert(
                "job_failed", f"job {report_type.value} {trading_date} gagal: {msg}"
            )
            return JobOutcome(
                "failed",
                report_type,
                trading_date,
                origin,
                job.id,
                reason=msg,
                warnings=gates.warnings,
            )

    # ------------------------------------------------------------------ pagi
    async def _morning(
        self, job: JobRun, trading_date: date, gates: GateResult, now: datetime
    ) -> JobOutcome:
        d = self.d
        try:
            session = d.calendar.previous_trading_session(trading_date)
        except CalendarCoverageError as exc:
            return await self._blocked(job, ReportType.MORNING, trading_date, (str(exc),), gates)
        watch = [w.symbol for w in await d.repo.watchlist()]
        open_signals = await d.repo.open_signals(self.origin.value)
        symbols = sorted({*watch, *(s.symbol for s in open_signals)})

        frames = await self._fetch_daily(symbols, session)
        universe: dict[str, SymbolInput] = {}
        data_blocked: list[BlockedInfo] = []
        quality: dict[str, QualityStatus] = {}
        providers: set[str] = set()
        for sym, agg in frames.items():
            if agg.frame is not None and agg.usable:
                if sym in watch:
                    universe[sym] = SymbolInput(
                        agg.frame,
                        agg.status,
                        foreign_flow_reason="tidak ada provider foreign flow aktif",
                    )
                quality[sym] = agg.status
                providers.add(agg.provider_used or "?")
            else:
                reason = f"{agg.status.value}: " + "; ".join(agg.issues)[:200]
                data_blocked.append(BlockedInfo(symbol=sym, stage="data", reason=reason))
        if watch and not universe:
            detail = "; ".join(f"{b.symbol}={b.reason}" for b in data_blocked)[:800]
            reason = f"semua data saham gagal/tidak layak: {detail}"
            await d.repo.mark_job(job.id, "failed", error=reason, data_session_date=session)
            await d.alerts.alert("data_all_failed", f"job pagi {trading_date}: {reason}")
            return JobOutcome(
                "failed",
                ReportType.MORNING,
                trading_date,
                self.origin,
                job.id,
                reason=reason,
                warnings=gates.warnings,
            )

        macro_snap, news_snap = await self._context()
        result = d.engine.run(session, universe)
        updates = await self._apply_lifecycle_daily(open_signals, frames, session)
        active = await self._active_snapshots()
        snapshot = build_snapshot(
            report_type=ReportType.MORNING,
            origin=self.origin,
            trading_date=trading_date,
            generated_at=now,
            engine_result=result,
            strategy_versions=d.engine.strategy_versions,
            quality_by_symbol=quality,
            providers_used=tuple(sorted(providers)),
            data_blocked=tuple(data_blocked),
            rules_label=d.rules.label,
            calendar_label=d.calendar.label,
            global_context=macro_snap,
            news=news_snap,
            entry_valid_sessions=d.lifecycle.entry_valid_sessions,
            active_signals=active,
            active_updates=updates,
            data_notes=tuple(gates.warnings),
        )
        await d.repo.save_signals(
            job, result.cards, app_env=d.settings.app_env, published_session=session
        )
        return await self._finish(job, snapshot, gates, session)

    # ------------------------------------------------------------------ sore
    async def _afternoon(
        self, job: JobRun, trading_date: date, gates: GateResult, now: datetime
    ) -> JobOutcome:
        d = self.d
        open_signals = await d.repo.open_signals(self.origin.value)
        updates: list[SignalUpdateSnapshot] = []
        quality: dict[str, QualityStatus] = {}
        data_blocked: list[BlockedInfo] = []
        providers: set[str] = set()
        last_prices: dict[str, tuple[Decimal, datetime]] = {}
        for sig in open_signals:
            agg = await d.aggregator.get_quote(sig.symbol)
            if not agg.usable or agg.quote is None:
                reason = f"{agg.status.value}: " + "; ".join(agg.issues)[:200]
                data_blocked.append(BlockedInfo(symbol=sig.symbol, stage="data", reason=reason))
                continue
            q = agg.quote
            quality[sig.symbol] = agg.status
            providers.add(agg.provider_used or "?")
            last_prices[sig.symbol] = (q.price, q.market_time)
            if q.open is None or q.high is None or q.low is None:
                data_blocked.append(
                    BlockedInfo(
                        symbol=sig.symbol, stage="data", reason="quote tanpa open/high/low intraday"
                    )
                )
                continue
            if (
                to_wib(q.market_time).date() != trading_date
                or trading_date <= sig.published_session
            ):
                continue
            bar = Bar(trading_date, q.open, q.high, q.low, q.price, partial=True)
            tr = await self._step_signal(sig, bar, q.market_time)
            if tr is not None:
                updates.append(tr)
        if open_signals and not quality:
            detail = "; ".join(f"{b.symbol}={b.reason}" for b in data_blocked)[:800]
            reason = f"semua quote intraday gagal/stale: {detail}"
            await d.repo.mark_job(job.id, "failed", error=reason, data_session_date=trading_date)
            await d.alerts.alert("data_all_failed", f"job sore {trading_date}: {reason}")
            return JobOutcome(
                "failed",
                ReportType.AFTERNOON,
                trading_date,
                self.origin,
                job.id,
                reason=reason,
                warnings=gates.warnings,
            )

        empty = EngineResult(
            session_date=trading_date,
            engine_version=ENGINE_VERSION,
            config_hash=d.engine.config_hash,
            cards=(),
            evaluations=(),
            blocked=(),
            universe=tuple(sorted(quality)),
            notes=(AFTERNOON_NOTE,),
        )
        active = await self._active_snapshots(last_prices)
        snapshot = build_snapshot(
            report_type=ReportType.AFTERNOON,
            origin=self.origin,
            trading_date=trading_date,
            generated_at=now,
            engine_result=empty,
            strategy_versions=d.engine.strategy_versions,
            quality_by_symbol=quality,
            providers_used=tuple(sorted(providers)),
            data_blocked=tuple(data_blocked),
            rules_label=d.rules.label,
            calendar_label=d.calendar.label,
            global_context=GlobalContextSnapshot(
                status="unavailable", fetched_at=None, reason="tidak diambil pada laporan sore"
            ),
            news=NewsSnapshot(status="not_configured"),
            entry_valid_sessions=d.lifecycle.entry_valid_sessions,
            active_signals=active,
            active_updates=tuple(updates),
            data_notes=tuple(gates.warnings),
        )
        return await self._finish(job, snapshot, gates, trading_date)

    # ------------------------------------------------------------------ pendukung
    async def _fetch_daily(self, symbols: list[str], session: date) -> dict[str, AggregatedOHLCV]:
        sem = asyncio.Semaphore(self.d.fetch_concurrency)

        async def one(sym: str) -> tuple[str, AggregatedOHLCV]:
            async with sem:
                agg = await self.d.aggregator.get_ohlcv(
                    sym, Timeframe.D1, None, None, expected_last_session=session
                )
                return sym, agg

        return dict(await asyncio.gather(*(one(s) for s in symbols)))

    async def _context(self) -> tuple[GlobalContextSnapshot, NewsSnapshot]:
        d = self.d
        macro_snap = GlobalContextSnapshot(
            status="unavailable", fetched_at=None, reason="provider makro tidak dikonfigurasi"
        )
        news_snap = NewsSnapshot(status="not_configured")
        if d.macro is not None:
            try:
                macro_snap = global_context_to_snapshot(await d.macro.snapshot())
            except Exception as exc:  # noqa: BLE001 - opsional: jangan menggagalkan job
                macro_snap = GlobalContextSnapshot(
                    status="unavailable", fetched_at=None, reason=redact_exception(exc)
                )
        if d.news is not None:
            try:
                news_snap = news_to_snapshot(await d.news.fetch_all())
            except Exception as exc:  # noqa: BLE001
                news_snap = NewsSnapshot(status="unavailable", errors={"*": redact_exception(exc)})
        return macro_snap, news_snap

    async def _step_signal(
        self, sig: Signal, bar: Bar, data_time: datetime
    ) -> SignalUpdateSnapshot | None:
        state = SignalState(
            status=SignalStatus(sig.status),
            entry_low=sig.entry_low,
            entry_high=sig.entry_high,
            stop_loss=sig.stop_loss,
            tp1=sig.tp1,
            published_session=sig.published_session,
            filled_price=sig.filled_price,
            filled_session=sig.filled_session,
            sessions_since_publish=sig.sessions_since_publish,
            sessions_held=sig.sessions_held,
        )
        new_state, transition = step(state, bar, self.d.lifecycle)
        if transition is None:
            if not bar.partial:
                await self.d.repo.touch_signal_counters(
                    sig.id,
                    sessions_since_publish=new_state.sessions_since_publish,
                    sessions_held=new_state.sessions_held,
                    last_session=bar.session_date,
                )
            return None
        from_pending = transition.previous is SignalStatus.PENDING_ENTRY
        await self.d.repo.apply_transition(
            sig.id,
            new_status=new_state.status.value,
            previous_status=transition.previous.value,
            trigger_price=transition.trigger_price,
            trigger_session=bar.session_date,
            note=transition.note,
            pnl_r=transition.pnl_r,
            data_time=data_time,
            filled_price=new_state.filled_price if from_pending else None,
            filled_session=new_state.filled_session if from_pending else None,
            sessions_since_publish=new_state.sessions_since_publish,
            sessions_held=new_state.sessions_held,
        )
        return SignalUpdateSnapshot(
            symbol=sig.symbol,
            previous_status=transition.previous.value,
            new_status=new_state.status.value,
            trigger_price=str(transition.trigger_price)
            if transition.trigger_price is not None
            else None,
            trigger_session=bar.session_date,
            note=transition.note,
            pnl_r=str(transition.pnl_r) if transition.pnl_r is not None else None,
        )

    async def _apply_lifecycle_daily(
        self, open_signals: list[Signal], frames: dict[str, AggregatedOHLCV], session: date
    ) -> tuple[SignalUpdateSnapshot, ...]:
        updates: list[SignalUpdateSnapshot] = []
        for sig in open_signals:
            already = (
                sig.last_evaluated_session is not None and sig.last_evaluated_session >= session
            )
            if session <= sig.published_session or already:
                continue
            agg = frames.get(sig.symbol)
            if agg is None or agg.frame is None or not agg.usable:
                continue
            bar = _bar_for_session(agg.frame, session)
            if bar is None:
                continue
            tr = await self._step_signal(sig, bar, agg.frame.fetched_at)
            if tr is not None:
                updates.append(tr)
        return tuple(updates)

    async def _active_snapshots(
        self, last_prices: dict[str, tuple[Decimal, datetime]] | None = None
    ) -> tuple[ActiveSignalSnapshot, ...]:
        rows = await self.d.repo.open_signals(self.origin.value)
        out = []
        for r in rows:
            lp = (last_prices or {}).get(r.symbol)
            out.append(
                ActiveSignalSnapshot(
                    symbol=r.symbol,
                    strategy=r.strategy,
                    status=r.status,
                    published_session=r.published_session,
                    entry_low=str(r.entry_low),
                    entry_high=str(r.entry_high),
                    stop_loss=str(r.stop_loss),
                    tp1=str(r.tp1),
                    filled_price=str(r.filled_price) if r.filled_price is not None else None,
                    last_price=str(lp[0]) if lp else None,
                    last_price_time=lp[1] if lp else None,
                )
            )
        return tuple(out)

    async def _blocked(
        self,
        job: JobRun,
        report_type: ReportType,
        trading_date: date,
        blockers: tuple[str, ...],
        gates: GateResult,
    ) -> JobOutcome:
        await self.d.repo.mark_job(job.id, "blocked", blockers=blockers)
        await self.d.alerts.alert(
            "job_blocked", f"job {report_type.value} {trading_date} diblokir: {'; '.join(blockers)}"
        )
        return JobOutcome(
            "blocked",
            report_type,
            trading_date,
            self.origin,
            job.id,
            reason="; ".join(blockers),
            blockers=blockers,
            warnings=gates.warnings,
        )

    async def _finish(
        self, job: JobRun, snapshot: ReportSnapshot, gates: GateResult, session: date
    ) -> JobOutcome:
        d = self.d
        parts = tuple(format_report(snapshot))
        await d.repo.mark_job(
            job.id, "running", snapshot_json=snapshot.to_json(), data_session_date=session
        )
        export_path = self._export(snapshot, parts)
        delivery: DeliverySummary | None = None
        live_sent = False
        if self.origin is SnapshotOrigin.LIVE:
            if gates.publish_allowed and d.notifier is not None:
                delivery = await self._deliver(job, parts)
                live_sent = delivery.sent > 0
                if delivery.unknown:
                    await d.alerts.alert(
                        "delivery_unknown",
                        f"job {job.id}: {delivery.unknown} pengiriman berstatus unknown; jalankan rekonsiliasi manual",
                    )
                if delivery.failed:
                    await d.alerts.alert(
                        "delivery_failed", f"job {job.id}: {delivery.failed} pengiriman gagal"
                    )
            else:
                await d.repo.mark_job(job.id, "blocked", blockers=gates.publish_blockers)
                await d.alerts.alert(
                    "publish_blocked",
                    f"job {job.id} tidak dikirim: {'; '.join(gates.publish_blockers)}",
                )
                return JobOutcome(
                    "blocked",
                    snapshot.report_type,
                    snapshot.trading_date,
                    self.origin,
                    job.id,
                    reason="; ".join(gates.publish_blockers),
                    blockers=gates.publish_blockers,
                    warnings=gates.warnings,
                    snapshot=snapshot,
                    parts=parts,
                    export_path=export_path,
                )
        await d.repo.mark_job(job.id, "completed")
        return JobOutcome(
            "completed",
            snapshot.report_type,
            snapshot.trading_date,
            self.origin,
            job.id,
            warnings=gates.warnings,
            snapshot=snapshot,
            parts=parts,
            delivery=delivery,
            export_path=export_path,
            live_sent=live_sent,
        )

    async def _deliver(self, job: JobRun, parts: tuple[str, ...]) -> DeliverySummary:
        d = self.d
        assert d.notifier is not None
        plan = [
            DeliveryPart(t.chat_id, t.thread_id or 0, i, len(parts), part)
            for t in d.settings.signal_targets
            for i, part in enumerate(parts)
        ]
        await d.repo.plan_deliveries(job.id, plan)
        pending = await d.repo.pending_deliveries(job.id)
        sent = failed = unknown = skipped = 0
        targets = {t.chat_id: t for t in d.settings.signal_targets}
        for row in pending:
            target = targets.get(row.chat_id)
            if target is None or not await d.repo.begin_sending(row.id):
                skipped += 1
                continue
            if row.part_index >= len(parts):
                await d.repo.finish_sending(row.id, "failed", error="bagian tidak ditemukan")
                failed += 1
                continue
            text = parts[row.part_index]
            try:
                result = await d.notifier.send(target, text)
                if result.status is DeliveryStatus.FAILED and result.retryable:
                    # RetryAfter dijamin belum terkirim: satu percobaan ulang terbatas.
                    await asyncio.sleep(min(30.0, _retry_after(result.error)))
                    result = await d.notifier.send(target, text)
            except TargetRejectedError as exc:
                await d.repo.finish_sending(row.id, "failed", error=f"tujuan ditolak: {exc}")
                failed += 1
                continue
            except Exception as exc:  # noqa: BLE001 - jangan biarkan status tetap 'sending'
                await d.repo.finish_sending(row.id, "unknown", error=redact_exception(exc))
                unknown += 1
                continue
            await d.repo.finish_sending(
                row.id,
                result.status.value,
                message_id=result.message_id,
                error=result.error or None,
            )
            if result.status is DeliveryStatus.SENT:
                sent += 1
            elif result.status is DeliveryStatus.FAILED:
                failed += 1
            else:
                unknown += 1
        return DeliverySummary(
            planned=len(plan), sent=sent, failed=failed, unknown=unknown, skipped=skipped
        )

    def _export(self, snapshot: ReportSnapshot, parts: tuple[str, ...]) -> Path | None:
        if self.d.export_dir is None:
            return None
        folder = self.d.export_dir / snapshot.origin.value
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{snapshot.trading_date.isoformat()}_{snapshot.report_type.value}.telegram.html"
        path = folder / name
        path.write_text(
            "\n\n<!-- ===== bagian berikutnya ===== -->\n\n".join(parts), encoding="utf-8"
        )
        return path


def _bar_for_session(frame: OHLCVFrame, session: date) -> Bar | None:
    df = frame.frame
    rows = df.loc[(df["session_date"] == session) & df["complete"].astype(bool)]
    if rows.empty:
        return None
    r = rows.iloc[-1]

    def dec(v: object) -> Decimal:
        return Decimal(repr(float(v)))  # type: ignore[arg-type]

    return Bar(
        session, dec(r["open"]), dec(r["high"]), dec(r["low"]), dec(r["close"]), partial=False
    )


def _retry_after(error: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*second", error)
    return float(m.group(1)) if m else 5.0
