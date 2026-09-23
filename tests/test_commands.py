from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest

from bot.alerts import AlertSink
from bot.commands import CommandDependencies, CommandRouter, IncomingCommand
from bot.reports import JobOutcome, ReportDependencies, ReportService
from data.aggregator import AggregatorConfig, MarketDataAggregator
from engine.pipeline import SignalEngine
from storage.repository import Database, Repository
from tests.conftest import FakeProvider

NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)
SIGNAL_CHAT = -1001234567890
ADMIN_CHAT = -1009876543210
ADMIN_USER = 42


@dataclass
class FakeService:
    calls: list[tuple[str, str]] = field(default_factory=list)
    d: Any = None

    async def run(self, report_type, *, trigger="schedule", now=None):
        self.calls.append((report_type.value, trigger))
        return JobOutcome("skipped", report_type, date(2026, 3, 16), reason="uji")


@pytest.fixture
async def env(tmp_path, settings_factory, market_rules, calendar):
    settings = settings_factory(
        telegram_signal_chat_ids=str(SIGNAL_CHAT),
        telegram_admin_chat_id=str(ADMIN_CHAT),
        telegram_admin_user_ids=str(ADMIN_USER),
        telegram_command_cooldown_seconds=60,
    )
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
    await db.init_dev_schema()
    repo = Repository(db, clock=lambda: NOW)
    await repo.seed_watchlist([("BBCA", "BCA")])
    service = FakeService()
    mono = {"t": 1000.0}
    router = CommandRouter(
        CommandDependencies(
            settings=settings,
            repo=repo,
            service=service,
            clock=lambda: NOW,
            monotonic=lambda: mono["t"],
        )  # type: ignore[arg-type]
    )
    yield router, repo, service, mono
    await db.dispose()


def cmd(
    command: str,
    *args: str,
    chat_id: int = SIGNAL_CHAT,
    chat_type: str = "supergroup",
    user_id: int | None = 7,
    **kw,
) -> IncomingCommand:
    return IncomingCommand(
        chat_id=chat_id,
        chat_type=chat_type,
        command=command,
        args=tuple(args),
        user_id=user_id,
        **kw,
    )


async def test_private_and_unknown_chats_get_no_reply(env) -> None:
    router, *_ = env
    assert await router.handle(cmd("start", chat_id=7, chat_type="private")) is None
    assert await router.handle(cmd("start", chat_id=-100555, chat_type="supergroup")) is None
    assert (
        await router.handle(
            cmd("runnow", "morning", chat_id=7, chat_type="private", user_id=ADMIN_USER)
        )
        is None
    )


async def test_member_commands_in_signal_group(env) -> None:
    router, *_ = env
    start = await router.handle(cmd("start"))
    assert start is not None and start.chat_id == SIGNAL_CHAT and "Bukan ajakan" in start.parts[0]
    help_ = await router.handle(cmd("help"))
    assert "Perintah admin" not in help_.parts[0]
    wl = await router.handle(cmd("watchlist", "list"))
    assert "BBCA" in wl.parts[0]
    status = await router.handle(cmd("status"))
    assert "Tidak ada sinyal" in status.parts[0]
    sig = await router.handle(cmd("signal"))
    assert "Belum ada laporan" in sig.parts[0]
    perf = await router.handle(cmd("performance", "7d"))
    assert "Belum ada sinyal yang ditutup" in perf.parts[0]
    bad = await router.handle(
        cmd("performance", "1y", chat_id=ADMIN_CHAT)
    )  # chat lain: tanpa cooldown
    assert "Format" in bad.parts[0]
    unknown = await router.handle(cmd("foo"))
    assert "tidak dikenal" in unknown.parts[0]


async def test_admin_commands_require_admin_chat_whitelist_and_identity(env) -> None:
    router, repo, service, _ = env
    # admin user di grup sinyal → ditolak
    r = await router.handle(cmd("runnow", "morning", user_id=ADMIN_USER))
    assert "hanya diterima di grup admin" in r.parts[0] and service.calls == []
    # non-admin di grup admin → ditolak
    r = await router.handle(cmd("pause", chat_id=ADMIN_CHAT, user_id=7))
    assert "whitelist" in r.parts[0] and await repo.is_paused() is False
    # admin anonim (sender_chat) → ditolak
    r = await router.handle(
        cmd("pause", chat_id=ADMIN_CHAT, user_id=ADMIN_USER, sender_chat_id=ADMIN_CHAT)
    )
    assert "tidak dapat diverifikasi" in r.parts[0]
    r = await router.handle(cmd("pause", chat_id=ADMIN_CHAT, user_id=1087968824))
    assert "tidak dapat diverifikasi" in r.parts[0]
    r = await router.handle(cmd("pause", chat_id=ADMIN_CHAT, user_id=ADMIN_USER, user_is_bot=True))
    assert "tidak dapat diverifikasi" in r.parts[0]
    # admin sah di grup admin → berhasil
    r = await router.handle(cmd("pause", "maintenance", chat_id=ADMIN_CHAT, user_id=ADMIN_USER))
    assert "di-pause" in r.parts[0] and await repo.is_paused() is True
    r = await router.handle(cmd("resume", chat_id=ADMIN_CHAT, user_id=ADMIN_USER))
    assert await repo.is_paused() is False
    r = await router.handle(cmd("runnow", "afternoon", chat_id=ADMIN_CHAT, user_id=ADMIN_USER))
    assert service.calls == [("afternoon", "manual")] and "skipped" in r.parts[0]
    r = await router.handle(cmd("runnow", "siang", chat_id=ADMIN_CHAT, user_id=ADMIN_USER))
    assert "Format" in r.parts[0]
    help_ = await router.handle(cmd("help", chat_id=ADMIN_CHAT, user_id=ADMIN_USER))
    assert "Perintah admin" in help_.parts[0]


async def test_watchlist_add_remove_is_admin_only(env) -> None:
    router, repo, *_ = env
    r = await router.handle(cmd("watchlist", "add", "tlkm", user_id=7))
    assert "Ditolak" in r.parts[0]
    r = await router.handle(
        cmd("watchlist", "add", "tlkm.jk", chat_id=ADMIN_CHAT, user_id=ADMIN_USER)
    )
    assert "TLKM ditambahkan" in r.parts[0]
    assert [w.symbol for w in await repo.watchlist()] == ["BBCA", "TLKM"]
    r = await router.handle(
        cmd("watchlist", "remove", "BBCA", chat_id=ADMIN_CHAT, user_id=ADMIN_USER)
    )
    assert "dihapus" in r.parts[0]
    r = await router.handle(
        cmd("watchlist", "add", "bb ca!", chat_id=ADMIN_CHAT, user_id=ADMIN_USER)
    )
    assert "tidak valid" in r.parts[0]


async def test_admin_commands_disabled_without_admin_chat(tmp_path, settings_factory) -> None:
    settings = settings_factory(
        telegram_signal_chat_ids=str(SIGNAL_CHAT), telegram_admin_user_ids=str(ADMIN_USER)
    )
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'd.db'}")
    await db.init_dev_schema()
    router = CommandRouter(
        CommandDependencies(settings=settings, repo=Repository(db), service=FakeService())
    )  # type: ignore[arg-type]
    r = await router.handle(cmd("pause", user_id=ADMIN_USER))
    assert r is not None and "belum dikonfigurasi" in r.parts[0]
    await db.dispose()


async def test_cooldown_for_expensive_commands(env) -> None:
    router, _, _, mono = env
    first = await router.handle(cmd("performance"))
    second = await router.handle(cmd("performance"))
    assert "Tunggu" in second.parts[0] and "Tunggu" not in first.parts[0]
    mono["t"] += 61
    third = await router.handle(cmd("performance"))
    assert "Tunggu" not in third.parts[0]
    # cooldown per chat: grup admin tidak terpengaruh
    other = await router.handle(cmd("performance", chat_id=ADMIN_CHAT))
    assert "Tunggu" not in other.parts[0]


async def test_cek_runs_engine_ad_hoc_and_labels_non_production(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    from tests.conftest import ok_frame
    from tests.engine_fixtures import trend_pullback_frame

    settings = settings_factory(telegram_signal_chat_ids=str(SIGNAL_CHAT))
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'e.db'}")
    await db.init_dev_schema()
    repo = Repository(db)
    frame = trend_pullback_frame("TRND")
    provider = FakeProvider("fx", ohlcv_results=lambda *a: ok_frame("fx", frame))
    aggregator = MarketDataAggregator([provider], AggregatorConfig(daily_min_history_bars=250))
    engine = SignalEngine(market_rules)
    service = ReportService(
        ReportDependencies(
            settings=settings,
            rules=market_rules,
            calendar=calendar,
            repo=repo,
            aggregator=aggregator,
            engine=engine,
            alerts=AlertSink(None, None),
            clock=lambda: NOW,
        )
    )
    router = CommandRouter(
        CommandDependencies(
            settings=settings,
            repo=repo,
            service=service,
            aggregator=aggregator,
            engine=engine,
            clock=lambda: NOW,
        )
    )
    r = await router.handle(cmd("cek", "trnd"))
    text = r.parts[0]
    assert "BUKAN sinyal produksi" in text and "TRND" in text and "Setup Trend Pullback" in text
    assert "Bukan ajakan" in text
    assert await repo.open_signals("dry_run") == []  # /cek tidak menyimpan sinyal
    cooled = await router.handle(cmd("cek", "trnd"))
    assert "Tunggu" in cooled.parts[0]  # cooldown melindungi command mahal
    await db.dispose()
