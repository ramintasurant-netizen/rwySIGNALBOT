"""Repository: klaim job atomik, pencatatan delivery, sinyal & lifecycle, watchlist, state.

Semua operasi memakai transaksi eksplisit. Klaim job dan dedup delivery bertumpu pada
UNIQUE constraint di database (bukan lock memori) agar aman terhadap proses ganda & restart.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.redaction import redact
from core.timeutil import utc_now
from engine.lifecycle import SignalStatus
from engine.models import SignalCard
from storage.models import (
    AppState,
    Base,
    JobRun,
    MessageDelivery,
    ProviderHealthRecord,
    Signal,
    SignalUpdate,
    Subscriber,
    WatchlistEntry,
)

REPLAYABLE_JOB_STATUSES = ("failed", "blocked")
STALE_JOB_TIMEOUT = timedelta(minutes=30)
PAUSE_KEY = "pause"
BACKTEST_GATE_KEY = "backtest_gate"


class Database:
    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        connect_args: dict[str, Any] = {}
        if url.startswith("sqlite"):
            connect_args["timeout"] = 30
        self.engine = create_async_engine(url, echo=echo, connect_args=connect_args)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_sqlite(self) -> bool:
        return self.url.startswith("sqlite")

    async def init_dev_schema(self) -> None:
        """Untuk development/test: ``create_all`` + PRAGMA. Production memakai Alembic."""
        async with self.engine.begin() as conn:
            if self.is_sqlite:
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.execute(text("PRAGMA busy_timeout=30000"))
            await conn.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()


@dataclass(frozen=True, slots=True)
class ClaimResult:
    job: JobRun | None
    reason: str = ""

    @property
    def claimed(self) -> bool:
        return self.job is not None


@dataclass(frozen=True, slots=True)
class DeliveryPart:
    chat_id: int
    thread_id: int
    part_index: int
    part_count: int
    content: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PerformanceStats:
    origin: str
    since: date | None
    total_closed: int
    wins: int
    losses: int
    win_rate: Decimal | None
    avg_r: Decimal | None
    sum_r: Decimal
    open_pending: int
    open_active: int
    expired: int


class Repository:
    def __init__(self, db: Database, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._db = db
        self._clock = clock

    def session(self) -> AsyncSession:
        return self._db.sessions()

    # ------------------------------------------------------------------ job runs
    async def claim_job(
        self,
        *,
        job_type: str,
        trading_date: date,
        origin: str,
        engine_version: str,
        config_hash: str,
        trigger: str = "schedule",
        stale_after: timedelta = STALE_JOB_TIMEOUT,
    ) -> ClaimResult:
        """Klaim (job_type, trading_date, origin) secara atomik.

        - Belum ada → INSERT (IntegrityError dari pesaing ⇒ tidak diklaim).
        - Ada dengan status failed/blocked → re-klaim lewat UPDATE bersyarat (attempt+1).
        - Ada dengan status claimed/running lebih lama dari ``stale_after`` → dianggap mati, re-klaim.
        - Selain itu (completed, atau sedang berjalan) → tidak diklaim.
        """
        now = self._clock()
        async with self.session() as s:
            async with s.begin():
                job = JobRun(
                    job_type=job_type,
                    trading_date=trading_date,
                    origin=origin,
                    trigger=trigger,
                    status="claimed",
                    attempt=1,
                    claimed_at=now,
                    engine_version=engine_version,
                    config_hash=config_hash,
                    blockers=[],
                )
                s.add(job)
                try:
                    await s.flush()
                except IntegrityError:
                    await s.rollback()
                else:
                    await s.commit()
                    return ClaimResult(job)
            # Sudah ada: coba re-klaim bersyarat.
            async with s.begin():
                existing = (
                    await s.execute(
                        select(JobRun).where(
                            JobRun.job_type == job_type,
                            JobRun.trading_date == trading_date,
                            JobRun.origin == origin,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    return ClaimResult(None, "job sedang diklaim proses lain")
                stale_cutoff = now - stale_after
                replayable = existing.status in REPLAYABLE_JOB_STATUSES or (
                    existing.status in ("claimed", "running") and existing.claimed_at < stale_cutoff
                )
                if not replayable:
                    return ClaimResult(
                        None, f"job sudah {existing.status} (attempt {existing.attempt})"
                    )
                result = await s.execute(
                    update(JobRun)
                    .where(
                        JobRun.id == existing.id,
                        JobRun.status == existing.status,
                        JobRun.attempt == existing.attempt,
                    )
                    .values(
                        status="claimed",
                        attempt=existing.attempt + 1,
                        claimed_at=now,
                        finished_at=None,
                        error=None,
                        blockers=[],
                        trigger=trigger,
                        engine_version=engine_version,
                        config_hash=config_hash,
                    )
                )
                if result.rowcount != 1:
                    return ClaimResult(None, "job direbut proses lain saat re-klaim")
                await s.refresh(existing)
                return ClaimResult(existing)

    async def mark_job(
        self,
        job_id: int,
        status: str,
        *,
        error: str | None = None,
        blockers: Sequence[str] = (),
        snapshot_json: str | None = None,
        data_session_date: date | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status}
        if status in ("completed", "failed", "blocked"):
            values["finished_at"] = self._clock()
        if error is not None:
            values["error"] = redact(error)
        if blockers:
            values["blockers"] = list(blockers)
        if snapshot_json is not None:
            values["snapshot_json"] = snapshot_json
        if data_session_date is not None:
            values["data_session_date"] = data_session_date
        async with self.session() as s, s.begin():
            await s.execute(update(JobRun).where(JobRun.id == job_id).values(**values))

    async def get_job(self, job_id: int) -> JobRun | None:
        async with self.session() as s:
            return await s.get(JobRun, job_id)

    async def latest_completed_job(self, job_type: str | None, origin: str) -> JobRun | None:
        async with self.session() as s:
            stmt = select(JobRun).where(JobRun.origin == origin, JobRun.status == "completed")
            if job_type:
                stmt = stmt.where(JobRun.job_type == job_type)
            stmt = stmt.order_by(JobRun.trading_date.desc(), JobRun.id.desc()).limit(1)
            return (await s.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ deliveries
    async def plan_deliveries(
        self, job_id: int, parts: Iterable[DeliveryPart], channel: str = "telegram"
    ) -> int:
        """Buat baris ``pending`` per (tujuan, bagian); baris yang sudah ada dibiarkan (idempoten)."""
        now = self._clock()
        created = 0
        for part in parts:
            async with self.session() as s:
                s.add(
                    MessageDelivery(
                        job_run_id=job_id,
                        chat_id=part.chat_id,
                        thread_id=part.thread_id,
                        part_index=part.part_index,
                        part_count=part.part_count,
                        channel=channel,
                        status="pending",
                        content_sha256=part.content_hash,
                        created_at=now,
                        updated_at=now,
                    )
                )
                try:
                    await s.commit()
                    created += 1
                except IntegrityError:
                    await s.rollback()  # sudah direncanakan sebelumnya (restart/proses lain)
        return created

    async def pending_deliveries(self, job_id: int) -> list[MessageDelivery]:
        async with self.session() as s:
            rows = await s.execute(
                select(MessageDelivery)
                .where(MessageDelivery.job_run_id == job_id, MessageDelivery.status == "pending")
                .order_by(
                    MessageDelivery.chat_id, MessageDelivery.thread_id, MessageDelivery.part_index
                )
            )
            return list(rows.scalars())

    async def deliveries_for_job(self, job_id: int) -> list[MessageDelivery]:
        async with self.session() as s:
            rows = await s.execute(
                select(MessageDelivery)
                .where(MessageDelivery.job_run_id == job_id)
                .order_by(MessageDelivery.id)
            )
            return list(rows.scalars())

    async def begin_sending(self, delivery_id: int) -> bool:
        """pending → sending secara atomik; False bila sudah diambil proses lain."""
        async with self.session() as s, s.begin():
            result = await s.execute(
                update(MessageDelivery)
                .where(MessageDelivery.id == delivery_id, MessageDelivery.status == "pending")
                .values(
                    status="sending",
                    attempts=MessageDelivery.attempts + 1,
                    updated_at=self._clock(),
                )
            )
            return result.rowcount == 1

    async def finish_sending(
        self,
        delivery_id: int,
        status: str,
        *,
        message_id: int | None = None,
        error: str | None = None,
    ) -> None:
        if status not in ("sent", "failed", "unknown", "pending"):
            raise ValueError(f"status delivery tidak valid: {status}")
        async with self.session() as s, s.begin():
            await s.execute(
                update(MessageDelivery)
                .where(MessageDelivery.id == delivery_id, MessageDelivery.status == "sending")
                .values(
                    status=status,
                    message_id=message_id,
                    error=redact(error) if error else None,
                    updated_at=self._clock(),
                )
            )

    async def mark_stale_sending_as_unknown(self) -> int:
        """Saat start: baris ``sending`` yang tertinggal (proses mati) menjadi ``unknown``."""
        async with self.session() as s, s.begin():
            result = await s.execute(
                update(MessageDelivery)
                .where(MessageDelivery.status == "sending")
                .values(
                    status="unknown",
                    error="proses berhenti saat mengirim",
                    updated_at=self._clock(),
                )
            )
            return int(result.rowcount or 0)

    async def unknown_deliveries(self) -> list[MessageDelivery]:
        async with self.session() as s:
            rows = await s.execute(
                select(MessageDelivery)
                .where(MessageDelivery.status == "unknown")
                .order_by(MessageDelivery.id)
            )
            return list(rows.scalars())

    async def resolve_unknown(self, delivery_id: int, status: str, *, resolved_by: str) -> bool:
        """Rekonsiliasi manual: unknown → sent (sudah dicek di grup) atau failed (boleh dikirim ulang eksplisit)."""
        if status not in ("sent", "failed"):
            raise ValueError("status rekonsiliasi harus sent atau failed")
        async with self.session() as s, s.begin():
            result = await s.execute(
                update(MessageDelivery)
                .where(MessageDelivery.id == delivery_id, MessageDelivery.status == "unknown")
                .values(status=status, resolved_by=resolved_by, updated_at=self._clock())
            )
            return result.rowcount == 1

    async def requeue_failed(self, delivery_id: int) -> bool:
        async with self.session() as s, s.begin():
            result = await s.execute(
                update(MessageDelivery)
                .where(MessageDelivery.id == delivery_id, MessageDelivery.status == "failed")
                .values(status="pending", error=None, updated_at=self._clock())
            )
            return result.rowcount == 1

    async def count_deliveries_by_status(self) -> dict[str, int]:
        async with self.session() as s:
            rows = await s.execute(
                select(MessageDelivery.status, func.count()).group_by(MessageDelivery.status)
            )
            return {status: int(n) for status, n in rows.all()}

    # ------------------------------------------------------------------ signals
    async def save_signals(
        self,
        job: JobRun,
        cards: Sequence[SignalCard],
        *,
        app_env: str,
        published_session: date,
    ) -> list[Signal]:
        now = self._clock()
        saved: list[Signal] = []
        async with self.session() as s, s.begin():
            for card in cards:
                r = card.risk
                row = Signal(
                    job_run_id=job.id,
                    origin=job.origin,
                    app_env=app_env,
                    symbol=card.symbol,
                    strategy=card.primary_strategy,
                    strategy_version=card.strategy_versions.get(card.primary_strategy, "?"),
                    engine_version=job.engine_version,
                    config_hash=job.config_hash,
                    confidence=card.confidence,
                    entry_low=r.entry_low,
                    entry_high=r.entry_high,
                    stop_loss=r.stop_loss,
                    tp1=r.tp1,
                    tp2=r.tp2,
                    tp3=r.tp3,
                    rr_tp1_gross=r.rr_tp1_gross,
                    rr_tp1_net=r.rr_tp1_net,
                    ara=r.ara,
                    arb=r.arb,
                    atr=r.atr,
                    sizing_json=_jsonable(asdict(r.sizing)) if r.sizing else None,
                    reasons_json=list(card.reasons),
                    evidence_json=_jsonable(card.evidence),
                    score_breakdown_json=dict(card.score_breakdown),
                    provider=card.data.provider,
                    bar_time=card.data.bar_time,
                    session_date=card.data.session_date,
                    fetched_at=card.data.fetched_at,
                    quality=card.data.quality.value,
                    status=SignalStatus.PENDING_ENTRY.value,
                    published_session=published_session,
                    created_at=now,
                )
                s.add(row)
                saved.append(row)
        return saved

    async def open_signals(self, origin: str) -> list[Signal]:
        async with self.session() as s:
            rows = await s.execute(
                select(Signal)
                .where(
                    Signal.origin == origin,
                    Signal.status.in_(
                        [SignalStatus.PENDING_ENTRY.value, SignalStatus.ACTIVE.value]
                    ),
                )
                .order_by(Signal.published_session.desc(), Signal.symbol)
            )
            return list(rows.scalars())

    async def apply_transition(
        self,
        signal_id: int,
        *,
        new_status: str,
        previous_status: str,
        trigger_price: Decimal | None,
        trigger_session: date,
        note: str,
        pnl_r: Decimal | None,
        data_time: datetime | None,
        filled_price: Decimal | None = None,
        filled_session: date | None = None,
        sessions_since_publish: int | None = None,
        sessions_held: int | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": new_status, "last_evaluated_session": trigger_session}
        if filled_price is not None:
            values["filled_price"] = filled_price
            values["filled_session"] = filled_session
        if new_status in (
            SignalStatus.CLOSED_TP.value,
            SignalStatus.CLOSED_SL.value,
            SignalStatus.CLOSED_TIME.value,
        ):
            values["closed_price"] = trigger_price
            values["closed_session"] = trigger_session
            values["pnl_r"] = pnl_r
        if sessions_since_publish is not None:
            values["sessions_since_publish"] = sessions_since_publish
        if sessions_held is not None:
            values["sessions_held"] = sessions_held
        async with self.session() as s, s.begin():
            await s.execute(update(Signal).where(Signal.id == signal_id).values(**values))
            s.add(
                SignalUpdate(
                    signal_id=signal_id,
                    previous_status=previous_status,
                    new_status=new_status,
                    trigger_price=trigger_price,
                    trigger_session=trigger_session,
                    data_time=data_time,
                    pnl_r=pnl_r,
                    note=note,
                    created_at=self._clock(),
                )
            )

    async def touch_signal_counters(
        self, signal_id: int, *, sessions_since_publish: int, sessions_held: int, last_session: date
    ) -> None:
        async with self.session() as s, s.begin():
            await s.execute(
                update(Signal)
                .where(Signal.id == signal_id)
                .values(
                    sessions_since_publish=sessions_since_publish,
                    sessions_held=sessions_held,
                    last_evaluated_session=last_session,
                )
            )

    async def performance(self, origin: str, since: date | None) -> PerformanceStats:
        closed_statuses = [
            SignalStatus.CLOSED_TP.value,
            SignalStatus.CLOSED_SL.value,
            SignalStatus.CLOSED_TIME.value,
        ]
        async with self.session() as s:
            stmt = select(Signal).where(Signal.origin == origin)
            if since is not None:
                stmt = stmt.where(Signal.published_session >= since)
            rows = list((await s.execute(stmt)).scalars())
        closed = [r for r in rows if r.status in closed_statuses and r.pnl_r is not None]
        wins = sum(1 for r in closed if r.pnl_r > 0)
        losses = sum(1 for r in closed if r.pnl_r <= 0)
        sum_r = sum((r.pnl_r for r in closed), Decimal("0"))
        n = len(closed)
        return PerformanceStats(
            origin=origin,
            since=since,
            total_closed=n,
            wins=wins,
            losses=losses,
            win_rate=(Decimal(wins) / n * 100).quantize(Decimal("0.1")) if n else None,
            avg_r=(sum_r / n).quantize(Decimal("0.01")) if n else None,
            sum_r=sum_r.quantize(Decimal("0.01")),
            open_pending=sum(1 for r in rows if r.status == SignalStatus.PENDING_ENTRY.value),
            open_active=sum(1 for r in rows if r.status == SignalStatus.ACTIVE.value),
            expired=sum(1 for r in rows if r.status == SignalStatus.EXPIRED.value),
        )

    # ------------------------------------------------------------------ watchlist
    async def seed_watchlist(self, entries: Iterable[tuple[str, str]]) -> int:
        """Isi awal dari YAML hanya bila tabel kosong (setelah itu admin yang mengelola)."""
        async with self.session() as s, s.begin():
            count = (await s.execute(select(func.count()).select_from(WatchlistEntry))).scalar_one()
            if count:
                return 0
            now = self._clock()
            added = 0
            for symbol, name in entries:
                s.add(WatchlistEntry(symbol=symbol, name=name, active=True, added_at=now))
                added += 1
            return added

    async def watchlist(self) -> list[WatchlistEntry]:
        async with self.session() as s:
            rows = await s.execute(
                select(WatchlistEntry)
                .where(WatchlistEntry.active.is_(True))
                .order_by(WatchlistEntry.symbol)
            )
            return list(rows.scalars())

    async def add_watchlist(self, symbol: str, *, user_id: int | None, name: str = "") -> bool:
        now = self._clock()
        async with self.session() as s, s.begin():
            existing = (
                await s.execute(select(WatchlistEntry).where(WatchlistEntry.symbol == symbol))
            ).scalar_one_or_none()
            if existing is None:
                s.add(
                    WatchlistEntry(
                        symbol=symbol,
                        name=name,
                        active=True,
                        added_by_user_id=user_id,
                        added_at=now,
                    )
                )
                return True
            if existing.active:
                return False
            existing.active = True
            existing.added_by_user_id = user_id
            existing.added_at = now
            existing.removed_at = None
            existing.removed_by_user_id = None
            return True

    async def remove_watchlist(self, symbol: str, *, user_id: int | None) -> bool:
        async with self.session() as s, s.begin():
            result = await s.execute(
                update(WatchlistEntry)
                .where(WatchlistEntry.symbol == symbol, WatchlistEntry.active.is_(True))
                .values(active=False, removed_by_user_id=user_id, removed_at=self._clock())
            )
            return result.rowcount == 1

    # ------------------------------------------------------------------ subscribers
    async def upsert_subscriber(
        self,
        *,
        chat_id: int,
        thread_id: int,
        kind: str,
        chat_type: str | None,
        title: str | None,
        verified: bool,
        note: str | None,
    ) -> None:
        now = self._clock()
        async with self.session() as s, s.begin():
            row = (
                await s.execute(select(Subscriber).where(Subscriber.chat_id == chat_id))
            ).scalar_one_or_none()
            if row is None:
                row = Subscriber(chat_id=chat_id, thread_id=thread_id, kind=kind, created_at=now)
                s.add(row)
            row.thread_id = thread_id
            row.kind = kind
            row.chat_type = chat_type
            row.title = title
            row.active = verified
            row.verified_at = now if verified else row.verified_at
            row.verification_note = note

    async def subscribers(self) -> list[Subscriber]:
        async with self.session() as s:
            return list(
                (
                    await s.execute(
                        select(Subscriber).order_by(Subscriber.kind, Subscriber.chat_id)
                    )
                ).scalars()
            )

    # ------------------------------------------------------------------ provider health
    async def record_provider_health(
        self, provider: str, healthy: bool, breaker_state: str, failures: int
    ) -> None:
        async with self.session() as s, s.begin():
            s.add(
                ProviderHealthRecord(
                    provider=provider,
                    healthy=healthy,
                    breaker_state=breaker_state,
                    consecutive_failures=failures,
                    checked_at=self._clock(),
                )
            )

    async def latest_provider_health(self) -> list[ProviderHealthRecord]:
        async with self.session() as s:
            sub = (
                select(
                    ProviderHealthRecord.provider, func.max(ProviderHealthRecord.id).label("mid")
                )
                .group_by(ProviderHealthRecord.provider)
                .subquery()
            )
            rows = await s.execute(
                select(ProviderHealthRecord).join(sub, ProviderHealthRecord.id == sub.c.mid)
            )
            return list(rows.scalars())

    # ------------------------------------------------------------------ app state
    async def get_state(self, key: str) -> dict[str, Any] | None:
        async with self.session() as s:
            row = await s.get(AppState, key)
            return dict(row.value_json) if row else None

    async def set_state(self, key: str, value: dict[str, Any]) -> None:
        async with self.session() as s, s.begin():
            row = await s.get(AppState, key)
            if row is None:
                s.add(AppState(key=key, value_json=value, updated_at=self._clock()))
            else:
                row.value_json = value
                row.updated_at = self._clock()

    async def is_paused(self) -> bool:
        state = await self.get_state(PAUSE_KEY)
        return bool(state and state.get("paused"))

    async def set_paused(self, paused: bool, *, by_user_id: int | None, reason: str = "") -> None:
        await self.set_state(
            PAUSE_KEY,
            {
                "paused": paused,
                "by_user_id": by_user_id,
                "reason": reason,
                "at": self._clock().isoformat(),
            },
        )

    async def backtest_gate(self) -> dict[str, Any] | None:
        return await self.get_state(BACKTEST_GATE_KEY)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
