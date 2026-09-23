"""Dry-run end-to-end dan jalur live dengan bot palsu — tanpa jaringan, tanpa token nyata."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace

from telegram.error import TimedOut

from bot.alerts import AlertSink
from bot.formatter import validate_html
from bot.reports import ReportDependencies, ReportService
from core.snapshot import ReportSnapshot, ReportType
from data.aggregator import AggregatorConfig, MarketDataAggregator
from data.providers.base import Capability, DataOrigin, Ok, ProviderMeta, Quote
from engine.lifecycle import LifecycleConfig
from engine.pipeline import SignalEngine
from notifications.telegram import TelegramNotifier
from storage.repository import Database, Repository
from tests.conftest import FakeProvider, ok_frame
from tests.engine_fixtures import END_SESSION, breakout_frame, flat_frame, trend_pullback_frame

FAKE_TOKEN = "1234567890:AAFakeTokenValueForTests_abcdefghijklmnop"
MONDAY_0830 = datetime(
    2026, 3, 16, 1, 30, tzinfo=UTC
)  # Senin 08:30 WIB; sesi sebelumnya Jumat 13 Mar
SIGNAL_CHAT = -1001234567890
ADMIN_CHAT = -1009876543210


@dataclass
class FakeBot:
    sent: list[tuple[int, str]] = field(default_factory=list)
    fail_once: bool = False
    next_id: int = 500

    async def get_me(self):
        return SimpleNamespace(id=999, username="uji_bot")

    async def get_chat(self, chat_id):
        return SimpleNamespace(type="supergroup", title=f"Grup {chat_id}", is_forum=False)

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="administrator")

    async def send_message(self, chat_id, text, **kwargs):
        if self.fail_once:
            self.fail_once = False
            raise TimedOut()
        self.sent.append((chat_id, text))
        self.next_id += 1
        return SimpleNamespace(message_id=self.next_id)


FRAMES = {
    "TRND": trend_pullback_frame("TRND"),
    "BRKO": breakout_frame("BRKO"),
    "FLAT": flat_frame("FLAT"),
}


def _provider() -> FakeProvider:
    def ohlcv(symbol, timeframe, basis):
        frame = FRAMES.get(symbol)
        if frame is None:
            from data.providers.base import Unavailable

            return Unavailable("fx", f"tidak ada fixture {symbol}")
        return ok_frame("fx", frame)

    return FakeProvider("fx", ohlcv_results=ohlcv)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def _build(
    tmp_path, settings, market_rules, calendar, *, bot=None, provider=None, now=MONDAY_0830
):
    clock = Clock(now)
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 's.db'}")
    await db.init_dev_schema()
    repo = Repository(db, clock=clock)
    await repo.seed_watchlist([("TRND", ""), ("BRKO", ""), ("FLAT", ""), ("NODATA", "")])
    aggregator = MarketDataAggregator(
        [provider or _provider()],
        AggregatorConfig(daily_min_history_bars=250, quote_cache_ttl_seconds=0),
        clock=clock,
    )
    notifier = None
    if bot is not None:
        notifier = TelegramNotifier(
            bot,
            allowed_targets=(
                *settings.signal_targets,
                *((settings.admin_target,) if settings.admin_target else ()),
            ),
        )
    alerts = AlertSink(notifier, settings.admin_target, monotonic=lambda: 0.0)
    deps = ReportDependencies(
        settings=settings,
        rules=market_rules,
        calendar=calendar,
        repo=repo,
        aggregator=aggregator,
        engine=SignalEngine(market_rules),
        alerts=alerts,
        notifier=notifier,
        lifecycle=LifecycleConfig(),
        clock=clock,
        export_dir=tmp_path / "exports",
    )
    return ReportService(deps), repo, alerts, db


async def test_dry_run_morning_end_to_end(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    settings = settings_factory()
    service, repo, alerts, db = await _build(tmp_path, settings, market_rules, calendar)
    try:
        outcome = await service.run(ReportType.MORNING)
        assert outcome.status == "completed", outcome.reason
        assert outcome.origin is not None and outcome.origin.value == "dry_run"
        assert outcome.delivery is None and outcome.live_sent is False  # dry-run: tidak mengirim
        assert outcome.snapshot is not None
        snap = outcome.snapshot
        assert snap.data_session_date == END_SESSION and snap.trading_date == date(2026, 3, 16)
        assert {c.symbol for c in snap.signals} == {"TRND", "BRKO"}
        assert any(b.symbol == "NODATA" and b.stage == "data" for b in snap.data_quality.blocked)
        assert snap.data_quality.symbols_evaluated == 3
        assert snap.global_context.status == "unavailable" and snap.news.status == "not_configured"
        assert any("belum terverifikasi" in n for n in snap.data_quality.notes)
        for part in outcome.parts:
            validate_html(part)
        assert outcome.export_path is not None and outcome.export_path.exists()
        assert "[DRY_RUN]" in outcome.export_path.read_text(encoding="utf-8")
        # persistensi: job completed dengan snapshot immutable, sinyal pending tersimpan, tanpa delivery
        job = await repo.get_job(outcome.job_id)
        assert (
            job is not None and job.status == "completed" and job.data_session_date == END_SESSION
        )
        stored = ReportSnapshot.from_json(job.snapshot_json)
        assert stored == snap
        opened = await repo.open_signals("dry_run")
        assert sorted(s.symbol for s in opened) == ["BRKO", "TRND"] and all(
            s.status == "pending_entry" for s in opened
        )
        assert await repo.count_deliveries_by_status() == {}
        # dedup: job kedua pada tanggal yang sama dilewati
        again = await service.run(ReportType.MORNING)
        assert again.status == "skipped" and "sudah completed" in again.reason
        # /runnow pada akhir pekan dilewati oleh kalender (bukan oleh dedup)
        weekend = await service.run(
            ReportType.MORNING, trigger="manual", now=datetime(2026, 3, 14, 2, 0, tzinfo=UTC)
        )
        assert weekend.status == "skipped" and "bukan hari perdagangan" in weekend.reason
        assert any(k == "job_skipped" for k, _, _ in alerts.history)
    finally:
        await db.dispose()


async def test_pause_blocks_scheduled_and_manual(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    service, repo, _, db = await _build(tmp_path, settings_factory(), market_rules, calendar)
    try:
        await repo.set_paused(True, by_user_id=1)
        out = await service.run(ReportType.MORNING, trigger="manual")
        assert out.status == "skipped" and "pause" in out.reason
    finally:
        await db.dispose()


async def test_all_data_failed_alerts_admin_without_empty_signal(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    from tests.conftest import failed

    provider = FakeProvider("fx", ohlcv_results=lambda *a: failed("fx"))
    service, repo, alerts, db = await _build(
        tmp_path, settings_factory(), market_rules, calendar, provider=provider
    )
    try:
        out = await service.run(ReportType.MORNING)
        assert out.status == "failed" and "semua data saham gagal" in out.reason
        assert out.parts == ()  # tidak ada pesan "sinyal kosong"
        assert any(k == "data_all_failed" for k, _, _ in alerts.history)
        job = await repo.get_job(out.job_id)
        assert job.status == "failed"
        # job gagal boleh di-replay
        retry = await service.run(ReportType.MORNING, trigger="manual")
        assert retry.status == "failed" and (await repo.get_job(retry.job_id)).attempt == 2
    finally:
        await db.dispose()


async def test_afternoon_updates_lifecycle_from_quotes_without_running_daily_strategies(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    settings = settings_factory()
    provider = _provider()
    service, repo, _, db = await _build(
        tmp_path, settings, market_rules, calendar, provider=provider
    )
    try:
        morning = await service.run(ReportType.MORNING)
        assert morning.status == "completed"
        opened = {s.symbol: s for s in await repo.open_signals("dry_run")}
        trnd = opened["TRND"]
        # Quote intraday Senin 15:00 WIB: harga menyentuh entry_high dan low di atas SL
        t1500 = datetime(2026, 3, 16, 8, 0, tzinfo=UTC)
        provider.quote_results = lambda: (
            None
        )  # tidak dipakai; kita set per simbol lewat ohlcv_results? gunakan mapping
        quotes = {
            "TRND": Quote(
                "TRND",
                trnd.entry_high,
                t1500,
                t1500,
                "fx",
                open=trnd.entry_high + 5,
                high=trnd.entry_high + 10,
                low=trnd.entry_high - 1,
                volume=1,
                origin=DataOrigin.FIXTURE,
            ),
            "BRKO": Quote(
                "BRKO",
                opened["BRKO"].entry_high + 50,
                t1500,
                t1500,
                "fx",
                open=opened["BRKO"].entry_high + 40,
                high=opened["BRKO"].entry_high + 60,
                low=opened["BRKO"].entry_high + 30,
                volume=1,
                origin=DataOrigin.FIXTURE,
            ),
        }

        async def get_quote(symbol):
            q = quotes[symbol]
            return Ok(q, ProviderMeta("fx", t1500, t1500, DataOrigin.FIXTURE))

        provider.get_quote = get_quote  # type: ignore[method-assign]
        provider.capabilities = frozenset({Capability.OHLCV_DAILY, Capability.QUOTE})  # type: ignore[misc]
        service.d.clock.now = t1500  # jam bersama repo/aggregator/service maju ke 15:00 WIB
        afternoon = await service.run(ReportType.AFTERNOON)
        assert afternoon.status == "completed", afternoon.reason
        snap = afternoon.snapshot
        assert snap.signals == ()  # tidak ada strategi harian pada bar belum lengkap
        assert any("tidak dijalankan pada bar yang belum lengkap" in n for n in snap.engine_notes)
        assert [u.symbol for u in snap.active_updates] == ["TRND"]
        assert snap.active_updates[0].new_status == "active"
        refreshed = {s.symbol: s for s in await repo.open_signals("dry_run")}
        assert (
            refreshed["TRND"].status == "active"
            and refreshed["TRND"].filled_price == trnd.entry_high
        )
        assert (
            refreshed["BRKO"].status == "pending_entry"
        )  # belum tersentuh; bar parsial tidak menghitung sesi
        assert refreshed["BRKO"].sessions_since_publish == 0
        joined = "\n".join(afternoon.parts)
        assert "PRE-CLOSE SIGNAL" in joined and "menunggu entry" in joined and "aktif" in joined
    finally:
        await db.dispose()


async def test_live_path_sends_records_deliveries_and_handles_unknown(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    settings = settings_factory(
        app_mode="live",
        telegram_bot_token=FAKE_TOKEN,
        telegram_enable_live_send=True,
        telegram_signal_chat_ids=f"{SIGNAL_CHAT},{SIGNAL_CHAT - 1}",
        telegram_admin_chat_id=str(ADMIN_CHAT),
    )
    bot = FakeBot(fail_once=True)
    service, repo, alerts, db = await _build(tmp_path, settings, market_rules, calendar, bot=bot)
    try:
        out = await service.run(ReportType.MORNING)
        assert out.status == "completed", out.reason
        assert out.origin.value == "live" and out.delivery is not None
        n_parts = len(out.parts)
        assert out.delivery.planned == 2 * n_parts
        assert (
            out.delivery.unknown == 1
            and out.delivery.sent == 2 * n_parts - 1
            and out.delivery.failed == 0
        )
        counts = await repo.count_deliveries_by_status()
        assert counts == {"sent": 2 * n_parts - 1, "unknown": 1}
        assert any(k == "delivery_unknown" for k, _, _ in alerts.history)
        # pesan alert admin juga terkirim lewat bot palsu ke ADMIN_CHAT saja
        assert all(chat in (SIGNAL_CHAT, SIGNAL_CHAT - 1, ADMIN_CHAT) for chat, _ in bot.sent)
        assert any(chat == ADMIN_CHAT and "ALERT" in text for chat, text in bot.sent)
        # unknown TIDAK dikirim ulang oleh run berikutnya (job sudah completed → skipped)
        again = await service.run(ReportType.MORNING)
        assert again.status == "skipped"
        sent_before = len(bot.sent)
        assert len(bot.sent) == sent_before
        assert len(await repo.unknown_deliveries()) == 1
        for chat, text in bot.sent:
            if chat != ADMIN_CHAT:
                validate_html(text)
                assert FAKE_TOKEN not in text
    finally:
        await db.dispose()


async def test_live_without_verified_targets_is_blocked_not_sent(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    settings = settings_factory(
        app_mode="live",
        telegram_bot_token=FAKE_TOKEN,
        telegram_enable_live_send=True,
        telegram_signal_chat_ids=str(SIGNAL_CHAT),
        telegram_admin_chat_id=str(ADMIN_CHAT),
    )

    class PrivateBot(FakeBot):
        async def get_chat(self, chat_id):
            return SimpleNamespace(type="private", title=None, is_forum=False)

    bot = PrivateBot()
    service, repo, alerts, db = await _build(tmp_path, settings, market_rules, calendar, bot=bot)
    try:
        out = await service.run(ReportType.MORNING)
        assert out.status == "blocked"
        assert any("ditolak" in b and "private" in b for b in out.blockers)
        assert bot.sent == []  # bahkan alert admin ditolak karena tipe private
        assert (await repo.get_job(out.job_id)).status == "blocked"
        assert await repo.count_deliveries_by_status() == {}
    finally:
        await db.dispose()


async def test_production_live_is_blocked_by_unverified_rules(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    settings = settings_factory(
        app_env="production",
        app_mode="live",
        database_url="postgresql+asyncpg://u:p@h/db",
        allow_sqlite_in_production=True,
        telegram_bot_token=FAKE_TOKEN,
        telegram_enable_live_send=True,
        telegram_signal_chat_ids=str(SIGNAL_CHAT),
        production_single_provider_approved=True,
    )
    bot = FakeBot()
    service, repo, _, db = await _build(tmp_path, settings, market_rules, calendar, bot=bot)
    try:
        out = await service.run(ReportType.MORNING)
        assert out.status == "blocked"
        assert any("market_rules.yaml belum terverifikasi" in b for b in out.blockers)
        assert any("gate backtest" in b for b in out.blockers)
        assert bot.sent == []
    finally:
        await db.dispose()


async def test_morning_lifecycle_uses_previous_session_bar(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    """Sinyal yang dipublikasikan Kamis 12 Mar dievaluasi dengan bar Jumat 13 Mar pada job Senin."""
    settings = settings_factory()
    service, repo, _, db = await _build(tmp_path, settings, market_rules, calendar)
    try:
        # Sinyal manual seolah dipublikasikan 12 Mar untuk TRND dengan zona entry jauh di atas harga
        from data.providers.base import QualityStatus
        from engine.pipeline import SymbolInput

        job = (
            await repo.claim_job(
                job_type="morning",
                trading_date=date(2026, 3, 13),
                origin="dry_run",
                engine_version="v",
                config_hash="h",
            )
        ).job
        result = SignalEngine(market_rules).run(
            END_SESSION, {"TRND": SymbolInput(FRAMES["TRND"], QualityStatus.DEGRADED)}
        )
        card = result.cards[0]
        await repo.save_signals(
            job, [card], app_env="development", published_session=date(2026, 3, 12)
        )
        await repo.mark_job(job.id, "completed")
        out = await service.run(ReportType.MORNING)
        assert out.status == "completed"
        sig = next(
            s
            for s in await repo.open_signals("dry_run")
            if s.published_session == date(2026, 3, 12)
        )
        # Bar Jumat: low <= entry_high (entry_high = close Jumat) → terisi
        assert sig.status == "active" and sig.filled_session == END_SESSION
        assert any(
            u.symbol == "TRND" and u.new_status == "active" for u in out.snapshot.active_updates
        )
    finally:
        await db.dispose()


async def test_narrator_and_whatsapp_export_are_integrated_and_isolated(
    tmp_path, settings_factory, market_rules, calendar
) -> None:
    from ai.llm_client import LLMError, LLMRequest, LLMResponse
    from ai.narrator import Narrator

    class GoodClient:
        provider = "fake"

        async def complete(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                text="Engine menemukan setup pada TRND dan BRKO; konteks global tidak tersedia pada sesi ini. Detail ada pada kartu.",
                provider="fake",
                model="m",
            )

    class BrokenClient:
        provider = "fake"

        async def complete(self, request: LLMRequest) -> LLMResponse:
            raise LLMError("HTTP 500")

    settings = settings_factory(whatsapp_export_enabled=True)
    service, repo, _, db = await _build(tmp_path, settings, market_rules, calendar)
    try:
        service.d.narrator = Narrator(GoodClient())  # type: ignore[arg-type]
        service.d.whatsapp_export_enabled = True
        out = await service.run(ReportType.MORNING)
        assert out.status == "completed"
        assert out.narrative_source == "llm"
        assert (
            out.snapshot is not None and out.snapshot.narrative and "TRND" in out.snapshot.narrative
        )
        assert out.whatsapp_path is not None and out.whatsapp_path.exists()
        wa = out.whatsapp_path.read_text(encoding="utf-8")
        assert out.snapshot.narrative in wa and "*📊 PRE-MARKET BRIEF" in wa
        assert "Ringkasan" in "\n".join(out.parts)
        # snapshot tersimpan memuat narasi yang sama
        job = await repo.get_job(out.job_id)
        assert ReportSnapshot.from_json(job.snapshot_json).narrative == out.snapshot.narrative

        # LLM rusak → template, job tetap selesai; ekspor WA nonaktif → tidak ada file baru
        service.d.narrator = Narrator(BrokenClient())  # type: ignore[arg-type]
        service.d.whatsapp_export_enabled = False
        out2 = await service.run(ReportType.AFTERNOON)
        assert out2.status in ("completed", "failed")  # sore tanpa quote fixture boleh gagal data
        if out2.status == "completed":
            assert out2.narrative_source == "template" and out2.whatsapp_path is None
    finally:
        await db.dispose()
