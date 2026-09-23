"""Gate penerbitan sinyal (ARCHITECTURE §6.3). Fail-closed: semua blocker harus kosong."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from config.market_rules import MarketRules
from config.settings import Settings
from config.trading_calendar import CalendarCoverageError, TradingCalendar
from notifications.base import TargetVerification


@dataclass(frozen=True, slots=True)
class GateResult:
    hard_blockers: tuple[str, ...]  # selalu memblokir job (kalender, pause)
    publish_blockers: tuple[str, ...]  # memblokir pengiriman live
    warnings: tuple[str, ...] = field(default=())

    @property
    def job_allowed(self) -> bool:
        return not self.hard_blockers

    @property
    def publish_allowed(self) -> bool:
        return not self.hard_blockers and not self.publish_blockers

    @property
    def all_blockers(self) -> tuple[str, ...]:
        return (*self.hard_blockers, *self.publish_blockers)


def evaluate_gates(
    settings: Settings,
    rules: MarketRules,
    calendar: TradingCalendar,
    trading_date: date,
    *,
    paused: bool,
    backtest_gate: dict[str, Any] | None,
    strategy_versions: dict[str, str],
    config_hash: str,
    target_verifications: dict[int, TargetVerification] | None,
) -> GateResult:
    hard: list[str] = []
    publish: list[str] = []
    warnings: list[str] = []

    try:
        if not calendar.is_trading_day(trading_date):
            hard.append(f"{trading_date} bukan hari perdagangan")
    except CalendarCoverageError as exc:
        hard.append(str(exc))
    if paused:
        hard.append("bot dalam status pause (admin)")

    strict = settings.is_production
    sink = publish if strict else warnings
    if not rules.verified:
        sink.append(f"market_rules.yaml belum terverifikasi ({rules.label})")
    if not calendar.verified:
        sink.append(f"trading_calendar.yaml belum terverifikasi ({calendar.label})")
    if settings.requires_cross_validation and len(settings.provider_order) < 2:
        publish.append(
            "produksi memerlukan ≥2 provider atau PRODUCTION_SINGLE_PROVIDER_APPROVED=true"
        )

    gate_msg = _backtest_gate_status(backtest_gate, strategy_versions, config_hash)
    if gate_msg:
        sink.append(gate_msg)

    if not settings.live_send_allowed:
        publish.append("pengiriman live nonaktif (APP_MODE/TELEGRAM_ENABLE_LIVE_SEND/token/tujuan)")
    elif target_verifications is not None:
        for target in settings.signal_targets:
            ver = target_verifications.get(target.chat_id)
            if ver is None:
                publish.append(f"tujuan {target} belum diverifikasi via API")
            elif not ver.ok:
                publish.append(f"tujuan {target} ditolak: {ver.reason}")
        if not settings.signal_targets:
            publish.append("tidak ada tujuan sinyal")
    return GateResult(tuple(hard), tuple(publish), tuple(warnings))


def _backtest_gate_status(
    gate: dict[str, Any] | None, strategy_versions: dict[str, str], config_hash: str
) -> str | None:
    if not gate:
        return "gate backtest belum tersedia: belum ada backtest valid yang lulus"
    if not gate.get("passed"):
        return "gate backtest: hasil terakhir TIDAK lulus"
    if gate.get("config_hash") != config_hash:
        return "gate backtest terikat konfigurasi lain (config_hash berbeda): kelayakan lama batal"
    gate_versions = gate.get("strategy_versions") or {}
    for sid, version in strategy_versions.items():
        if gate_versions.get(sid) != version:
            return f"gate backtest tidak mencakup {sid} v{version}"
    return None
