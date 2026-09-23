"""Perakitan komponen (settings → DB → aggregator → engine → notifier → service → router)."""

from __future__ import annotations

import asyncio
import signal
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from telegram.ext import Application

from bot.alerts import AlertSink
from bot.commands import CommandDependencies, CommandRouter
from bot.handlers import register_handlers
from bot.reports import ReportDependencies, ReportService
from bot.scheduler import build_scheduler, health_tick, write_heartbeat
from config.market_rules import MarketRules, load_market_rules
from config.settings import Settings
from config.trading_calendar import TradingCalendar, load_trading_calendar
from config.watchlist import load_watchlist
from core.redaction import redact_exception
from data.aggregator import AggregatorConfig, MarketDataAggregator
from data.providers import build_providers
from data.providers.global_macro import GlobalMacroProvider, load_global_macro_config
from data.providers.news import NewsProvider, load_news_sources
from engine.lifecycle import LifecycleConfig
from engine.pipeline import SignalEngine
from engine.risk import RiskConfig
from engine.scorer import ScorerConfig
from notifications.base import TargetRejectedError
from notifications.telegram import BotClient, TelegramNotifier
from storage.repository import Database, Repository


@dataclass
class Runtime:
    settings: Settings
    rules: MarketRules
    calendar: TradingCalendar
    db: Database
    repo: Repository
    aggregator: MarketDataAggregator
    engine: SignalEngine
    alerts: AlertSink
    service: ReportService
    router: CommandRouter
    notifier: TelegramNotifier | None
    app: Application | None
    heartbeat_path: Path


async def build_runtime(
    settings: Settings, *, bot: BotClient | None = None, with_bot: bool = True
) -> Runtime:
    cfg = settings.config_dir
    rules = load_market_rules(cfg / "market_rules.yaml")
    calendar = load_trading_calendar(cfg / "trading_calendar.yaml")
    watchlist = load_watchlist(cfg / "watchlist.yaml")
    macro_cfg = load_global_macro_config(cfg / "global_macro.yaml")
    news_cfg = load_news_sources(cfg / "news_sources.yaml")

    settings.var_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_url)
    if db.is_sqlite:
        await db.init_dev_schema()  # production PostgreSQL memakai Alembic (README)
    repo = Repository(db)
    seeded = await repo.seed_watchlist((e.symbol, e.name) for e in watchlist.symbols)
    if seeded:
        logger.info("watchlist awal diisi dari YAML: {} simbol", seeded)
    stale = await repo.mark_stale_sending_as_unknown()
    if stale:
        logger.warning("{} pengiriman 'sending' dari proses sebelumnya ditandai unknown", stale)

    aggregator = MarketDataAggregator(
        build_providers(settings, rules), AggregatorConfig.from_settings(settings)
    )
    engine = SignalEngine(
        rules, risk=RiskConfig.from_settings(settings), scorer=ScorerConfig.from_settings(settings)
    )

    app: Application | None = None
    notifier: TelegramNotifier | None = None
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ""
    if with_bot and (bot is not None or token):
        if bot is None:
            app = Application.builder().token(token).build()
            bot = app.bot
        notifier = TelegramNotifier(
            bot,
            allowed_targets=(
                *settings.signal_targets,
                *((settings.admin_target,) if settings.admin_target else ()),
            ),
        )
    alerts = AlertSink(notifier, settings.admin_target)

    macro = GlobalMacroProvider(
        macro_cfg,
        max_concurrency=settings.data_max_concurrency,
        timeout_seconds=settings.data_request_timeout_seconds,
    )
    news = NewsProvider(news_cfg.enabled_sources) if news_cfg.enabled_sources else None
    export_dir = settings.var_dir / "exports"
    service = ReportService(
        ReportDependencies(
            settings=settings,
            rules=rules,
            calendar=calendar,
            repo=repo,
            aggregator=aggregator,
            engine=engine,
            alerts=alerts,
            notifier=notifier,
            macro=macro,
            news=news,
            lifecycle=LifecycleConfig(),
            export_dir=export_dir,
        )
    )

    async def broadcast(html: str) -> str:
        if notifier is None:
            return "notifier tidak tersedia"
        results = []
        for target in settings.signal_targets:
            try:
                res = await notifier.send(target, html)
                results.append(f"{target}: {res.status.value}")
            except TargetRejectedError as exc:
                results.append(f"{target}: ditolak ({exc})")
        return "; ".join(results) or "tidak ada tujuan"

    router = CommandRouter(
        CommandDependencies(
            settings=settings,
            repo=repo,
            service=service,
            aggregator=aggregator,
            engine=engine,
            broadcast=broadcast,
        )
    )
    if app is not None:
        register_handlers(app, router)
    return Runtime(
        settings,
        rules,
        calendar,
        db,
        repo,
        aggregator,
        engine,
        alerts,
        service,
        router,
        notifier,
        app,
        settings.var_dir / "heartbeat",
    )


async def verify_and_record_targets(rt: Runtime) -> None:
    if rt.notifier is None:
        return
    targets = [(t, "signal") for t in rt.settings.signal_targets]
    if rt.settings.admin_target is not None:
        targets.append((rt.settings.admin_target, "admin"))
    for target, kind in targets:
        ver = await rt.notifier.verify_target(target, force=True)
        await rt.repo.upsert_subscriber(
            chat_id=target.chat_id,
            thread_id=target.thread_id or 0,
            kind=kind,
            chat_type=ver.chat_type,
            title=ver.title,
            verified=ver.ok,
            note=ver.reason or None,
        )
        (logger.info if ver.ok else logger.error)(
            "tujuan {} ({}): {} {}", target, kind, "OK" if ver.ok else "DITOLAK", ver.reason
        )


async def run_forever(rt: Runtime) -> None:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    async def health() -> None:
        await health_tick(rt.aggregator, rt.repo, rt.alerts, rt.heartbeat_path)

    scheduler = build_scheduler(rt.settings, rt.service, health=health)
    write_heartbeat(rt.heartbeat_path)
    if rt.app is not None:
        await rt.app.initialize()
        await verify_and_record_targets(rt)
        await rt.app.start()
        assert rt.app.updater is not None
        await rt.app.updater.start_polling(drop_pending_updates=True)
        logger.info("polling Telegram aktif (grup-only)")
    else:
        logger.warning(
            "tanpa TELEGRAM_BOT_TOKEN: hanya scheduler dry-run yang berjalan (ekspor ke var/exports)"
        )
    scheduler.start()
    for job in scheduler.get_jobs():
        logger.info("jadwal {}: berikutnya {}", job.id, job.next_run_time)
    try:
        await stop.wait()
    finally:
        logger.info("shutdown tertib dimulai")
        scheduler.shutdown(wait=True)
        if rt.app is not None:
            try:
                if rt.app.updater is not None and rt.app.updater.running:
                    await rt.app.updater.stop()
                if rt.app.running:
                    await rt.app.stop()
                await rt.app.shutdown()
            except Exception as exc:  # noqa: BLE001
                logger.error("shutdown bot: {}", redact_exception(exc))
        await rt.db.dispose()
