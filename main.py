"""Entrypoint stock_signal_bot.

Tahap 2: perintah diagnostik tanpa token —
  python main.py config                 ringkasan konfigurasi tersanitasi + status verifikasi
  python main.py health                 health check provider aktif (JARINGAN)
  python main.py fetch --symbol BBCA    ambil & validasi OHLCV harian lewat aggregator (JARINGAN)
  python main.py evaluate [--symbols ...] jalankan engine pada watchlist, cetak kartu (JARINGAN, tanpa kirim)
Perintah `run` (bot + scheduler) tersedia pada Tahap 4.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from config.common import ConfigError
from config.market_rules import load_market_rules
from config.settings import Settings, load_settings
from config.trading_calendar import CalendarCoverageError, load_trading_calendar
from config.watchlist import load_watchlist
from core.logging import configure_logging
from core.timeutil import WIB
from data.aggregator import AggregatorConfig, MarketDataAggregator
from data.providers import build_providers
from data.providers.base import Timeframe
from data.providers.global_macro import load_global_macro_config
from data.providers.news import load_news_sources
from engine.pipeline import SignalEngine, SymbolInput
from engine.risk import RiskConfig
from engine.scorer import ScorerConfig


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def production_blockers(settings: Settings, config_dir: Path) -> list[str]:
    """Daftar alasan sinyal produksi TIDAK boleh diterbitkan dengan konfigurasi saat ini."""
    blockers: list[str] = []
    try:
        rules = load_market_rules(config_dir / "market_rules.yaml")
        if not rules.verified:
            blockers.append(f"market_rules.yaml belum terverifikasi ({rules.label})")
    except ConfigError as exc:
        blockers.append(str(exc))
    try:
        calendar = load_trading_calendar(config_dir / "trading_calendar.yaml")
        if not calendar.verified:
            blockers.append(f"trading_calendar.yaml belum terverifikasi ({calendar.label})")
        today = datetime.now(WIB).date()
        try:
            calendar.is_trading_day(today)
        except CalendarCoverageError as exc:
            blockers.append(str(exc))
    except ConfigError as exc:
        blockers.append(str(exc))
    if not settings.live_send_allowed:
        blockers.append(
            "pengiriman live nonaktif (APP_MODE/TELEGRAM_ENABLE_LIVE_SEND/token/tujuan)"
        )
    if settings.requires_cross_validation and len(settings.provider_order) < 2:
        blockers.append(
            "produksi memerlukan >=2 provider atau PRODUCTION_SINGLE_PROVIDER_APPROVED=true"
        )
    blockers.append("gate backtest belum tersedia (Tahap 6)")
    blockers.append("verifikasi tujuan Telegram via API belum tersedia (Tahap 4)")
    return blockers


def cmd_config(settings: Settings) -> int:
    config_dir = settings.config_dir
    summary = settings.sanitized_summary()
    verification = {}
    for name, loader in (
        ("market_rules", load_market_rules),
        ("trading_calendar", load_trading_calendar),
        ("watchlist", load_watchlist),
        ("global_macro", load_global_macro_config),
        ("news_sources", load_news_sources),
    ):
        try:
            cfg = loader(config_dir / f"{name}.yaml")
            verification[name] = {"verified": cfg.meta.verified, "label": cfg.meta.display_label}
        except ConfigError as exc:
            verification[name] = {"error": str(exc)}
    print(
        _json(
            {
                "settings": summary,
                "config_verification": verification,
                "production_blockers": production_blockers(settings, config_dir),
            }
        )
    )
    return 0


async def cmd_health(settings: Settings) -> int:
    rules = load_market_rules(settings.config_dir / "market_rules.yaml")
    aggregator = MarketDataAggregator(
        build_providers(settings, rules), AggregatorConfig.from_settings(settings)
    )
    report = await aggregator.health()
    print(_json([h.__dict__ for h in report]))
    return 0 if all(h.healthy for h in report) else 1


async def cmd_fetch(settings: Settings, symbol: str, timeframe: str) -> int:
    rules = load_market_rules(settings.config_dir / "market_rules.yaml")
    calendar = load_trading_calendar(settings.config_dir / "trading_calendar.yaml")
    tf = Timeframe(timeframe)
    aggregator = MarketDataAggregator(
        build_providers(settings, rules), AggregatorConfig.from_settings(settings)
    )
    expected = None
    if tf is Timeframe.D1:
        try:
            expected = calendar.previous_trading_session(datetime.now(WIB).date())
        except CalendarCoverageError as exc:
            logger.warning("kalender: {}", exc)
    result = await aggregator.get_ohlcv(symbol, tf, None, None, expected_last_session=expected)
    out = {
        "symbol": result.symbol,
        "timeframe": result.timeframe.value,
        "status": result.status.value,
        "usable": result.usable,
        "provider_used": result.provider_used,
        "providers_tried": list(result.providers_tried),
        "issues": list(result.issues),
        "expected_last_session": expected,
    }
    if result.frame is not None:
        tail = result.frame.frame.tail(3).copy()
        tail.index = [ts.tz_convert(WIB).isoformat() for ts in tail.index]
        out["bars"] = len(result.frame)
        out["last_complete_session"] = result.frame.last_complete_session
        out["tail"] = tail[["open", "high", "low", "close", "volume", "complete"]].to_dict("index")
        out["notes"] = list(result.frame.notes)
    print(_json(out))
    return 0 if result.usable else 1


async def cmd_evaluate(settings: Settings, symbols: list[str] | None) -> int:
    """Jalankan engine pada watchlist (atau --symbols) memakai data Yahoo. Tidak mengirim apa pun."""
    rules = load_market_rules(settings.config_dir / "market_rules.yaml")
    calendar = load_trading_calendar(settings.config_dir / "trading_calendar.yaml")
    watchlist = load_watchlist(settings.config_dir / "watchlist.yaml")
    universe_symbols = tuple(symbols) if symbols else watchlist.codes
    try:
        session = calendar.previous_trading_session(datetime.now(WIB).date())
    except CalendarCoverageError as exc:
        print(f"Kalender: {exc}", file=sys.stderr)
        return 1
    aggregator = MarketDataAggregator(
        build_providers(settings, rules), AggregatorConfig.from_settings(settings)
    )
    engine = SignalEngine(
        rules, risk=RiskConfig.from_settings(settings), scorer=ScorerConfig.from_settings(settings)
    )
    fetched = await asyncio.gather(
        *(
            aggregator.get_ohlcv(sym, Timeframe.D1, None, None, expected_last_session=session)
            for sym in universe_symbols
        )
    )
    universe: dict[str, SymbolInput] = {}
    data_blocked: list[dict[str, object]] = []
    for agg in fetched:
        if agg.frame is None or not agg.usable:
            data_blocked.append(
                {"symbol": agg.symbol, "status": agg.status.value, "issues": list(agg.issues)}
            )
            continue
        universe[agg.symbol] = SymbolInput(
            agg.frame, agg.status, foreign_flow_reason="tidak ada provider foreign flow aktif"
        )
    result = engine.run(session, universe)
    out = {
        "session_date": session,
        "engine_version": result.engine_version,
        "config_hash": result.config_hash,
        "rules_label": rules.label,
        "calendar_label": calendar.label,
        "universe": list(universe_symbols),
        "data_blocked": data_blocked,
        "engine_blocked": [b.__dict__ for b in result.blocked],
        "evaluated_without_setup": [
            e.symbol for e in result.evaluations if e.card is None and e.blocked is None
        ],
        "strategy_states": {
            e.symbol: {o.strategy_id: o.state.value for o in e.outcomes}
            for e in result.evaluations
            if e.outcomes
        },
        "notes": list(result.notes),
        "cards": [
            {
                "symbol": c.symbol,
                "strategy": c.primary_strategy,
                "confidence": c.confidence,
                "breakdown": c.score_breakdown,
                "entry": [c.risk.entry_low, c.risk.entry_high],
                "stop_loss": c.risk.stop_loss,
                "tp": [c.risk.tp1, c.risk.tp2, c.risk.tp3],
                "rr_tp1": {"gross": c.risk.rr_tp1_gross, "net": c.risk.rr_tp1_net},
                "ara_arb": [c.risk.ara, c.risk.arb],
                "sizing": c.risk.sizing.__dict__ if c.risk.sizing else None,
                "reasons": list(c.reasons),
                "risk_notes": list(c.risk.notes),
                "data": c.data.__dict__,
            }
            for c in result.cards
        ],
        "disclaimer": "Bukan ajakan jual/beli. Analisis bersifat informasional. Keputusan dan risiko sepenuhnya milik Anda.",
    }
    print(_json(out))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="stock_signal_bot")
    parser.add_argument(
        "--env-file", default=".env", help="berkas .env (default: .env; '-' untuk mengabaikan)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="tampilkan konfigurasi tersanitasi dan status verifikasi")
    sub.add_parser("health", help="health check provider (membutuhkan jaringan)")
    fetch = sub.add_parser("fetch", help="ambil & validasi OHLCV lewat aggregator (jaringan)")
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--timeframe", default="1d", choices=[t.value for t in Timeframe])
    evaluate = sub.add_parser(
        "evaluate", help="jalankan engine pada watchlist tanpa mengirim (jaringan)"
    )
    evaluate.add_argument("--symbols", nargs="*", help="override watchlist, mis. BBCA BBRI")
    sub.add_parser("run", help="jalankan bot + scheduler (Tahap 4)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_file = None if args.env_file == "-" else args.env_file
    try:
        settings = load_settings(env_file)
    except Exception as exc:  # noqa: BLE001 - tampilkan error konfigurasi dengan jelas
        print(f"Konfigurasi tidak valid:\n{exc}", file=sys.stderr)
        return 2
    configure_logging(level=settings.log_level, diagnose=settings.log_diagnose)
    for warning in settings.config_warnings:
        logger.warning(warning)

    if args.command == "config":
        return cmd_config(settings)
    if args.command == "health":
        return asyncio.run(cmd_health(settings))
    if args.command == "fetch":
        return asyncio.run(cmd_fetch(settings, args.symbol, args.timeframe))
    if args.command == "evaluate":
        return asyncio.run(cmd_evaluate(settings, args.symbols))
    if args.command == "run":
        print(
            "Perintah `run` belum tersedia: bot, storage, dan scheduler dibangun pada Tahap 4.",
            file=sys.stderr,
        )
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
