"""APScheduler (AsyncIOScheduler) untuk job pagi/sore WIB, health check berkala, heartbeat."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from bot.alerts import AlertSink
from bot.reports import ReportService
from config.settings import Settings
from core.snapshot import ReportType
from core.timeutil import WIB, parse_hhmm, utc_now
from data.aggregator import MarketDataAggregator
from storage.repository import Repository


def write_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(utc_now().isoformat(), encoding="utf-8")


async def health_tick(
    aggregator: MarketDataAggregator, repo: Repository, alerts: AlertSink, heartbeat: Path
) -> None:
    report = await aggregator.health()
    for h in report:
        await repo.record_provider_health(
            h.provider, h.healthy, h.breaker_state.value, h.consecutive_failures
        )
        if not h.healthy:
            await alerts.alert(
                f"provider_unhealthy:{h.provider}",
                f"provider {h.provider} tidak sehat (breaker {h.breaker_state.value})",
            )
    unknown = (await repo.count_deliveries_by_status()).get("unknown", 0)
    if unknown:
        await alerts.alert(
            "delivery_unknown_pending",
            f"{unknown} pengiriman berstatus unknown menunggu rekonsiliasi",
        )
    write_heartbeat(heartbeat)


def build_scheduler(
    settings: Settings,
    service: ReportService,
    *,
    health: Callable[[], Awaitable[None]] | None = None,
    health_interval_minutes: int = 15,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=WIB)
    morning, afternoon = (
        parse_hhmm(settings.schedule_morning),
        parse_hhmm(settings.schedule_afternoon),
    )

    async def run_morning() -> None:
        outcome = await service.run(ReportType.MORNING)
        logger.info("job pagi selesai: {} {}", outcome.status, outcome.reason)

    async def run_afternoon() -> None:
        outcome = await service.run(ReportType.AFTERNOON)
        logger.info("job sore selesai: {} {}", outcome.status, outcome.reason)

    # Kalender/libur diperiksa di dalam service (preflight); cron hanya membatasi Senin–Jumat.
    scheduler.add_job(
        run_morning,
        CronTrigger(day_of_week="mon-fri", hour=morning.hour, minute=morning.minute, timezone=WIB),
        id="morning_report",
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_afternoon,
        CronTrigger(
            day_of_week="mon-fri", hour=afternoon.hour, minute=afternoon.minute, timezone=WIB
        ),
        id="afternoon_report",
        misfire_grace_time=600,
        coalesce=True,
        max_instances=1,
    )
    if health is not None:
        scheduler.add_job(
            health,
            IntervalTrigger(minutes=health_interval_minutes),
            id="health_check",
            coalesce=True,
            max_instances=1,
        )
    return scheduler
