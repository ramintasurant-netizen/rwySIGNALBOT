from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from data.providers.base import QualityStatus
from engine.lifecycle import SignalStatus
from engine.pipeline import SignalEngine, SymbolInput
from storage.repository import Database, DeliveryPart, Repository
from tests.engine_fixtures import END_SESSION, trend_pullback_frame

NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


async def _make_repo(url: str) -> tuple[Repository, Clock, Database]:
    db = Database(url)
    await db.init_dev_schema()
    clock = Clock()
    return Repository(db, clock=clock), clock, db


@pytest.fixture
async def repo(tmp_path):
    repo, clock, db = await _make_repo(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    yield repo, clock
    await db.dispose()


async def test_claim_job_is_exclusive_and_replayable_only_after_failure(repo) -> None:
    r, clock = repo
    kwargs = dict(
        job_type="morning",
        trading_date=date(2026, 3, 16),
        origin="dry_run",
        engine_version="0.3.0",
        config_hash="abc",
    )
    first = await r.claim_job(**kwargs)
    assert first.claimed and first.job is not None and first.job.attempt == 1
    second = await r.claim_job(**kwargs)
    assert not second.claimed and "sudah claimed" in second.reason
    await r.mark_job(first.job.id, "completed", snapshot_json="{}")
    third = await r.claim_job(**kwargs)
    assert not third.claimed and "completed" in third.reason
    # Origin berbeda adalah kunci berbeda (dry_run vs live tidak saling memblokir).
    other = await r.claim_job(**{**kwargs, "origin": "live"})
    assert other.claimed
    await r.mark_job(
        other.job.id,
        "failed",
        error="token 1234567890:AAFakeTokenValueForTests_abcdefghijklmnop bocor",
    )
    replay = await r.claim_job(**{**kwargs, "origin": "live"}, trigger="manual")
    assert replay.claimed and replay.job.attempt == 2 and replay.job.trigger == "manual"
    stored = await r.get_job(other.job.id)
    assert stored is not None and "AAFakeToken" not in (stored.error or "")


async def test_stale_running_job_can_be_reclaimed(repo) -> None:
    r, clock = repo
    kwargs = dict(
        job_type="afternoon",
        trading_date=date(2026, 3, 16),
        origin="live",
        engine_version="v",
        config_hash="h",
    )
    first = await r.claim_job(**kwargs)
    await r.mark_job(first.job.id, "running")
    assert not (await r.claim_job(**kwargs)).claimed
    clock.now = NOW + timedelta(minutes=31)
    stale = await r.claim_job(**kwargs)
    assert stale.claimed and stale.job.attempt == 2


async def test_concurrent_claims_exactly_one_winner(repo) -> None:
    r, _ = repo
    kwargs = dict(
        job_type="morning",
        trading_date=date(2026, 3, 17),
        origin="live",
        engine_version="v",
        config_hash="h",
    )
    results = await asyncio.gather(*(r.claim_job(**kwargs) for _ in range(8)))
    assert sum(1 for res in results if res.claimed) == 1


async def test_delivery_plan_is_idempotent_and_states_are_atomic(repo) -> None:
    r, _ = repo
    job = (
        await r.claim_job(
            job_type="morning",
            trading_date=date(2026, 3, 16),
            origin="live",
            engine_version="v",
            config_hash="h",
        )
    ).job
    parts = [
        DeliveryPart(-1001, 0, 0, 2, "bagian 1"),
        DeliveryPart(-1001, 0, 1, 2, "bagian 2"),
        DeliveryPart(-1002, 7, 0, 2, "x"),
    ]
    assert await r.plan_deliveries(job.id, parts) == 3
    assert await r.plan_deliveries(job.id, parts) == 0  # restart: tidak menggandakan
    pending = await r.pending_deliveries(job.id)
    assert [(d.chat_id, d.thread_id, d.part_index) for d in pending] == [
        (-1002, 7, 0),
        (-1001, 0, 0),
        (-1001, 0, 1),
    ]
    first = pending[0]
    assert await r.begin_sending(first.id) is True
    assert await r.begin_sending(first.id) is False  # sudah diambil
    await r.finish_sending(first.id, "sent", message_id=42)
    second = pending[1]
    assert await r.begin_sending(second.id)
    await r.finish_sending(second.id, "unknown", error="timeout setelah request dikirim")
    third = pending[2]
    assert await r.begin_sending(third.id)
    await r.finish_sending(third.id, "failed", error="chat not found")
    counts = await r.count_deliveries_by_status()
    assert counts == {"sent": 1, "unknown": 1, "failed": 1}
    assert await r.pending_deliveries(job.id) == []
    with pytest.raises(ValueError):
        await r.finish_sending(third.id, "delivered")


async def test_restart_marks_sending_as_unknown_and_reconciliation(repo) -> None:
    r, _ = repo
    job = (
        await r.claim_job(
            job_type="morning",
            trading_date=date(2026, 3, 16),
            origin="live",
            engine_version="v",
            config_hash="h",
        )
    ).job
    await r.plan_deliveries(
        job.id, [DeliveryPart(-1001, 0, 0, 1, "a"), DeliveryPart(-1002, 0, 0, 1, "b")]
    )
    pending = await r.pending_deliveries(job.id)
    await r.begin_sending(pending[0].id)  # proses "mati" di sini
    assert await r.mark_stale_sending_as_unknown() == 1
    unknown = await r.unknown_deliveries()
    assert len(unknown) == 1 and unknown[0].error == "proses berhenti saat mengirim"
    assert (
        len(await r.pending_deliveries(job.id)) == 1
    )  # yang belum dikirim tetap pending, TIDAK unknown
    assert await r.resolve_unknown(unknown[0].id, "sent", resolved_by="admin:42") is True
    assert await r.resolve_unknown(unknown[0].id, "failed", resolved_by="admin:42") is False
    with pytest.raises(ValueError):
        await r.resolve_unknown(unknown[0].id, "pending", resolved_by="x")
    # failed → pending hanya lewat requeue eksplisit
    await r.begin_sending(pending[1].id)
    await r.finish_sending(pending[1].id, "failed", error="x")
    assert await r.requeue_failed(pending[1].id) is True
    assert len(await r.pending_deliveries(job.id)) == 1


async def test_signals_persist_with_decimal_and_lifecycle_updates(repo, market_rules) -> None:
    r, _ = repo
    job = (
        await r.claim_job(
            job_type="morning",
            trading_date=date(2026, 3, 16),
            origin="dry_run",
            engine_version="0.3.0",
            config_hash="h",
        )
    ).job
    result = SignalEngine(market_rules).run(
        END_SESSION, {"TRND": SymbolInput(trend_pullback_frame(), QualityStatus.DEGRADED)}
    )
    assert result.cards
    saved = await r.save_signals(
        job, result.cards, app_env="development", published_session=END_SESSION
    )
    assert len(saved) == 1
    open_ = await r.open_signals("dry_run")
    assert len(open_) == 1 and open_[0].status == "pending_entry"
    row = open_[0]
    assert isinstance(row.entry_high, Decimal) and row.entry_high == result.cards[0].risk.entry_high
    assert row.bar_time.tzinfo is not None or row.bar_time == result.cards[0].data.bar_time.replace(
        tzinfo=None
    )
    assert await r.open_signals("live") == []  # pemisahan origin
    await r.apply_transition(
        row.id,
        new_status=SignalStatus.ACTIVE.value,
        previous_status="pending_entry",
        trigger_price=row.entry_high,
        trigger_session=date(2026, 3, 16),
        note="isi",
        pnl_r=None,
        data_time=NOW,
        filled_price=row.entry_high,
        filled_session=date(2026, 3, 16),
        sessions_since_publish=1,
        sessions_held=1,
    )
    await r.apply_transition(
        row.id,
        new_status=SignalStatus.CLOSED_TP.value,
        previous_status="active",
        trigger_price=row.tp1,
        trigger_session=date(2026, 3, 18),
        note="tp",
        pnl_r=Decimal("2.33"),
        data_time=NOW,
    )
    perf = await r.performance("dry_run", None)
    assert (
        perf.total_closed == 1
        and perf.wins == 1
        and perf.win_rate == Decimal("100.0")
        and perf.avg_r == Decimal("2.33")
    )
    assert (await r.performance("live", None)).total_closed == 0
    async with r.session() as s:
        from sqlalchemy import select

        from storage.models import Signal, SignalUpdate

        sig = (await s.execute(select(Signal))).scalar_one()
        assert (
            sig.status == "closed_tp"
            and sig.closed_price == row.tp1
            and sig.filled_price == row.entry_high
        )
        updates = list((await s.execute(select(SignalUpdate).order_by(SignalUpdate.id))).scalars())
        assert [(u.previous_status, u.new_status) for u in updates] == [
            ("pending_entry", "active"),
            ("active", "closed_tp"),
        ]


async def test_watchlist_seed_and_admin_ops(repo) -> None:
    r, _ = repo
    assert await r.seed_watchlist([("BBCA", "Bank BCA"), ("BBRI", "")]) == 2
    assert await r.seed_watchlist([("TLKM", "")]) == 0  # sudah ada isi
    assert [w.symbol for w in await r.watchlist()] == ["BBCA", "BBRI"]
    assert await r.add_watchlist("TLKM", user_id=42) is True
    assert await r.add_watchlist("TLKM", user_id=42) is False
    assert await r.remove_watchlist("BBRI", user_id=42) is True
    assert await r.remove_watchlist("BBRI", user_id=42) is False
    assert [w.symbol for w in await r.watchlist()] == ["BBCA", "TLKM"]
    assert await r.add_watchlist("BBRI", user_id=43) is True  # reaktivasi


async def test_pause_state_and_backtest_gate(repo) -> None:
    r, _ = repo
    assert await r.is_paused() is False
    await r.set_paused(True, by_user_id=42, reason="maintenance")
    assert await r.is_paused() is True
    state = await r.get_state("pause")
    assert state is not None and state["by_user_id"] == 42
    await r.set_paused(False, by_user_id=42)
    assert await r.is_paused() is False
    assert await r.backtest_gate() is None


async def test_subscribers_and_provider_health(repo) -> None:
    r, _ = repo
    await r.upsert_subscriber(
        chat_id=-1001,
        thread_id=0,
        kind="signal",
        chat_type="supergroup",
        title="Grup",
        verified=True,
        note=None,
    )
    await r.upsert_subscriber(
        chat_id=-1001,
        thread_id=5,
        kind="signal",
        chat_type="supergroup",
        title="Grup",
        verified=False,
        note="bot bukan admin",
    )
    subs = await r.subscribers()
    assert (
        len(subs) == 1
        and subs[0].thread_id == 5
        and subs[0].active is False
        and subs[0].verified_at is not None
    )
    await r.record_provider_health("yahoo", True, "closed", 0)
    await r.record_provider_health("yahoo", False, "open", 3)
    latest = await r.latest_provider_health()
    assert len(latest) == 1 and latest[0].healthy is False and latest[0].breaker_state == "open"


POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="TEST_POSTGRES_URL tidak diset")
async def test_postgres_claim_and_delivery_semantics() -> None:
    repo, _, db = await _make_repo(POSTGRES_URL)  # type: ignore[arg-type]
    try:
        kwargs = dict(
            job_type="morning",
            trading_date=date(2099, 1, 1),
            origin="fixture",
            engine_version="v",
            config_hash="h",
        )
        results = await asyncio.gather(*(repo.claim_job(**kwargs) for _ in range(6)))
        assert sum(1 for res in results if res.claimed) == 1
    finally:
        await db.dispose()
