"""Entrypoint stock_signal_bot.

Tahap 2: perintah diagnostik tanpa token —
  python main.py config                 ringkasan konfigurasi tersanitasi + status verifikasi
  python main.py health                 health check provider aktif (JARINGAN)
  python main.py fetch --symbol BBCA    ambil & validasi OHLCV harian lewat aggregator (JARINGAN)
  python main.py evaluate [--symbols ...] jalankan engine pada watchlist, cetak kartu (JARINGAN, tanpa kirim)
  python main.py dryrun morning|afternoon  jalankan satu job end-to-end tanpa kirim; ekspor ke var/exports (JARINGAN)
  python main.py run                    bot Telegram (grup-only) + scheduler; tanpa token = scheduler dry-run saja
  python main.py screen --style bsjp|bpjs [--top 5] [--lookback 60]
                                        statistik historis gap overnight / intraday untuk kandidat (JARINGAN)
  python main.py research --start ... --end ... [--fetch] [--oos-for VARIAN]
                                        grid varian HANYA in-sample; OOS sekali untuk varian pilihan
  python main.py backtest --start ... --end ... [--csv-dir DIR] [--save-gate]
                                        backtest engine yang sama; laporan + CSV ke var/backtests (JARINGAN bila tanpa CSV)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from loguru import logger

from config.common import ConfigError
from config.market_rules import load_market_rules
from config.settings import Settings, load_settings
from config.trading_calendar import CalendarCoverageError, load_trading_calendar
from config.watchlist import load_watchlist
from core.logging import configure_logging
from core.snapshot import ReportType
from core.timeutil import WIB
from data.aggregator import AggregatorConfig, MarketDataAggregator
from data.providers import build_providers
from data.providers.base import Timeframe
from data.providers.global_macro import load_global_macro_config
from data.providers.news import load_news_sources
from engine.pipeline import SignalEngine, SymbolInput
from engine.regime import RegimeConfig
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
    print(_json([asdict(h) for h in report]))
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
        rules,
        risk=RiskConfig.from_settings(settings),
        scorer=ScorerConfig.from_settings(settings),
        regime=RegimeConfig.from_settings(settings),
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
        "engine_blocked": [asdict(b) for b in result.blocked],
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
                "sizing": asdict(c.risk.sizing) if c.risk.sizing else None,
                "reasons": list(c.reasons),
                "risk_notes": list(c.risk.notes),
                "data": asdict(c.data),
            }
            for c in result.cards
        ],
        "disclaimer": "Bukan ajakan jual/beli. Analisis bersifat informasional. Keputusan dan risiko sepenuhnya milik Anda.",
    }
    print(_json(out))
    return 0


async def cmd_dryrun(settings: Settings, which: str) -> int:
    """Satu job end-to-end dengan origin dry_run: tidak mengirim apa pun, mencetak pesan tersanitasi."""
    from bot.commands import outcome_text
    from bot.runtime import build_runtime

    if settings.app_mode != "dry_run":
        print(
            "dryrun hanya untuk APP_MODE=dry_run (ubah .env atau pakai --env-file -)",
            file=sys.stderr,
        )
        return 2
    rt = await build_runtime(settings, with_bot=False)
    try:
        outcome = await rt.service.run(ReportType(which), trigger="manual")
    finally:
        await rt.db.dispose()
    print(f"status: {outcome.status}")
    if outcome.reason:
        print(f"alasan: {outcome.reason}")
    for w in outcome.warnings:
        print(f"peringatan: {w}")
    if outcome.export_path:
        print(f"ekspor: {outcome.export_path}")
    if outcome.whatsapp_path:
        print(f"ekspor whatsapp: {outcome.whatsapp_path}")
    if outcome.narrative_source:
        print(f"narasi: {outcome.narrative_source}")
    for i, part in enumerate(outcome.parts, 1):
        print(f"\n----- bagian {i}/{len(outcome.parts)} ({len(part)} karakter) -----\n{part}")
    logger.info(outcome_text(outcome).replace("<", "[").replace(">", "]"))
    return 0 if outcome.status == "completed" else 1


async def cmd_run(settings: Settings) -> int:
    from bot.runtime import build_runtime, run_forever

    rt = await build_runtime(settings)
    await run_forever(rt)
    return 0


async def cmd_backtest(settings: Settings, args: argparse.Namespace) -> int:
    from decimal import Decimal

    from backtest.data import fetch_frames, load_csv_dir
    from backtest.gate import GateThresholds, evaluate_gate, gate_record, split_period
    from backtest.metrics import write_equity_csv, write_trades_csv
    from backtest.runner import BacktestConfig, BacktestRunner
    from storage.repository import Database, Repository

    rules = load_market_rules(settings.config_dir / "market_rules.yaml")
    watchlist = load_watchlist(settings.config_dir / "watchlist.yaml")
    engine = SignalEngine(
        rules,
        risk=RiskConfig.from_settings(settings),
        scorer=ScorerConfig.from_settings(settings),
        regime=RegimeConfig.from_settings(settings),
    )
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    symbols = [s.upper() for s in (args.symbols or watchlist.codes)]
    skipped: dict[str, str] = {}
    if args.csv_dir:
        frames = load_csv_dir(Path(args.csv_dir))
        if args.symbols:
            frames = {k: v for k, v in frames.items() if k in symbols}
    else:
        aggregator = MarketDataAggregator(
            build_providers(settings, rules), AggregatorConfig.from_settings(settings)
        )
        frames, skipped = await fetch_frames(
            aggregator, symbols, start=start, end=end, min_bars=settings.daily_min_history_bars
        )
    if not frames:
        print(f"Tidak ada data layak untuk backtest: {skipped}", file=sys.stderr)
        return 1
    index_frame = None
    if engine.regime.enabled:
        if args.csv_dir:
            idx_path = Path(args.csv_dir) / f"{engine.regime.index_symbol.lstrip('^')}.index.csv"
            if idx_path.exists():
                from backtest.data import load_csv_frame

                index_frame = load_csv_frame(
                    idx_path, symbol=None, raw_symbol=engine.regime.index_symbol
                )
            else:
                logger.warning(
                    "filter rezim aktif tetapi {} tidak ada; rezim 'unknown'", idx_path.name
                )
        else:
            idx_agg = await aggregator.get_ohlcv(
                engine.regime.index_symbol,
                Timeframe.D1,
                datetime.combine(start, datetime.min.time(), tzinfo=UTC)
                - timedelta(days=int(engine.regime.warmup * 1.6) + 30),
                datetime.combine(end, datetime.min.time(), tzinfo=UTC) + timedelta(days=1),
                min_bars=engine.regime.warmup,
            )
            index_frame = idx_agg.frame if idx_agg.frame is not None and idx_agg.usable else None
            if index_frame is None:
                logger.warning(
                    "data indeks {} tidak layak: {}", engine.regime.index_symbol, idx_agg.issues
                )

    thresholds = GateThresholds(
        min_trades=args.min_trades,
        min_profit_factor=Decimal(str(args.min_pf)),
        max_drawdown_pct=Decimal(str(args.max_dd)),
        oos_fraction=Decimal(str(args.oos_fraction)),
    )
    (is_start, is_end), (oos_start, oos_end) = split_period(start, end, thresholds.oos_fraction)
    cfg_common = dict(
        initial_capital=settings.sizing_capital_example,
        slippage_ticks=args.slippage_ticks,
        fee_buy_pct=settings.risk_fee_buy_pct,
        fee_sell_pct=settings.risk_fee_sell_pct,
        warmup_bars=settings.daily_min_history_bars,
    )

    def progress(session: date, i: int, n: int) -> None:
        if i == 1 or i == n or i % 50 == 0:
            logger.info("backtest {} {}/{}", session, i, n)

    def run_segment(seg_start: date, seg_end: date):
        cfg = BacktestConfig(start=seg_start, end=seg_end, **cfg_common)
        return BacktestRunner(engine, rules, cfg, progress=progress).run(
            frames, index_frame=index_frame
        )

    try:
        in_sample = run_segment(is_start, is_end)
    except ValueError as exc:
        logger.warning("in-sample tidak dapat dijalankan: {}", exc)
        in_sample = None
    try:
        oos = run_segment(oos_start, oos_end)
    except ValueError as exc:
        print(f"Out-of-sample tidak dapat dijalankan: {exc}", file=sys.stderr)
        return 1
    evaluation = evaluate_gate(oos, thresholds, in_sample=in_sample)
    record = gate_record(oos, evaluation)

    out_dir = (
        settings.var_dir / "backtests" / f"{start.isoformat()}_{end.isoformat()}_{oos.result_hash}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    write_equity_csv(oos.equity, out_dir / "oos_equity.csv")
    write_trades_csv(oos.trades, out_dir / "oos_trades.csv")
    if in_sample is not None:
        write_equity_csv(in_sample.equity, out_dir / "in_sample_equity.csv")
        write_trades_csv(in_sample.trades, out_dir / "in_sample_trades.csv")
    report = {
        "in_sample": in_sample.summary() if in_sample else None,
        "out_of_sample": oos.summary(),
        "gate": record,
        "data_skipped": skipped,
        "rules_label": rules.label,
        "output_dir": str(out_dir),
    }
    (out_dir / "report.json").write_text(_json(report), encoding="utf-8")
    print(_json(report))

    if args.save_gate:
        db = Database(settings.database_url)
        if db.is_sqlite:
            await db.init_dev_schema()
        try:
            await Repository(db).set_state("backtest_gate", record)
        finally:
            await db.dispose()
        print(
            f"\nGate {'LULUS' if record['passed'] else 'TIDAK LULUS'} disimpan ke app_state.backtest_gate "
            f"(config_hash {record['config_hash']}). Hasil backtest tidak menjamin keuntungan masa depan.",
            file=sys.stderr,
        )
    return 0 if evaluation.passed else 4


async def cmd_screen(settings: Settings, args: argparse.Namespace) -> int:
    """Peringkat statistik BSJP/BPJS atas watchlist ∪ kandidat. Bukan sinyal, bukan prediksi."""
    from engine.short_term import DISCLAIMER as ST_DISCLAIMER
    from engine.short_term import compute_short_term_stats, rank_short_term

    rules = load_market_rules(settings.config_dir / "market_rules.yaml")
    calendar = load_trading_calendar(settings.config_dir / "trading_calendar.yaml")
    watchlist = load_watchlist(settings.config_dir / "watchlist.yaml")
    candidates_path = settings.config_dir / "universe_candidates.yaml"
    candidates = load_watchlist(candidates_path).codes if candidates_path.exists() else ()
    symbols = list(args.symbols) if args.symbols else sorted({*watchlist.codes, *candidates})
    try:
        session = calendar.previous_trading_session(datetime.now(WIB).date())
    except CalendarCoverageError as exc:
        print(f"Kalender: {exc}", file=sys.stderr)
        return 1
    aggregator = MarketDataAggregator(
        build_providers(settings, rules), AggregatorConfig.from_settings(settings)
    )
    sem = asyncio.Semaphore(settings.data_max_concurrency)

    async def one(sym: str):
        async with sem:
            return sym, await aggregator.get_ohlcv(
                sym,
                Timeframe.D1,
                None,
                None,
                expected_last_session=session,
                min_bars=args.lookback + 30,
            )

    results = await asyncio.gather(*(one(s) for s in symbols))
    stats = []
    skipped: dict[str, str] = {}
    for sym, agg in results:
        if agg.frame is None or not agg.usable:
            skipped[sym] = f"{agg.status.value}: " + "; ".join(agg.issues)[:120]
            continue
        st = compute_short_term_stats(
            agg.frame, lookback=args.lookback, min_avg_value=rules.liquidity.min_avg_daily_value_idr
        )
        if st is None:
            skipped[sym] = "histori kurang untuk lookback"
        else:
            stats.append(st)
    ranked = rank_short_term(stats, args.style, top=args.top)
    print(
        _json(
            {
                "style": args.style,
                "session": session,
                "lookback_sessions": args.lookback,
                "evaluated": len(stats),
                "skipped": skipped,
                "liquidity_min_avg_value": str(rules.liquidity.min_avg_daily_value_idr),
                "ranking": [st.as_dict() for st in ranked],
                "disclaimer": ST_DISCLAIMER,
                "rules_label": rules.label,
            }
        )
    )
    return 0


async def cmd_research(settings: Settings, args: argparse.Namespace) -> int:
    from decimal import Decimal

    from backtest.data import fetch_frames
    from backtest.gate import GateThresholds, evaluate_gate, split_period
    from backtest.research import (
        DEFAULT_VARIANTS,
        cache_dir,
        format_table,
        load_cached,
        run_variants,
        save_frame,
    )
    from backtest.runner import BacktestConfig

    rules = load_market_rules(settings.config_dir / "market_rules.yaml")
    watchlist = load_watchlist(settings.config_dir / "watchlist.yaml")
    risk = RiskConfig.from_settings(settings)
    regime_cfg = RegimeConfig.from_settings(settings)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    folder = cache_dir(settings.var_dir)
    frames, index_frame = load_cached(folder)
    if args.fetch or not frames:
        symbols = [s.upper() for s in (args.symbols or watchlist.codes)]
        aggregator = MarketDataAggregator(
            build_providers(settings, rules), AggregatorConfig.from_settings(settings)
        )
        frames, skipped = await fetch_frames(
            aggregator, symbols, start=start, end=end, min_bars=settings.daily_min_history_bars
        )
        if skipped:
            logger.warning("simbol dilewati: {}", skipped)
        idx = await aggregator.get_ohlcv(
            regime_cfg.index_symbol,
            Timeframe.D1,
            datetime.combine(start, datetime.min.time(), tzinfo=UTC)
            - timedelta(days=int(regime_cfg.warmup * 1.6) + 30),
            datetime.combine(end, datetime.min.time(), tzinfo=UTC) + timedelta(days=1),
            min_bars=regime_cfg.warmup,
        )
        index_frame = idx.frame if idx.frame is not None and idx.usable else None
        for f in frames.values():
            save_frame(f, folder)
        if index_frame is not None:
            save_frame(index_frame, folder)
        logger.info(
            "cache riset: {} saham + indeks={} di {}", len(frames), index_frame is not None, folder
        )
    if not frames:
        print("Tidak ada data untuk riset.", file=sys.stderr)
        return 1

    thresholds = GateThresholds(oos_fraction=Decimal(str(args.oos_fraction)))
    (is_start, is_end), (oos_start, oos_end) = split_period(start, end, thresholds.oos_fraction)
    common = dict(
        initial_capital=settings.sizing_capital_example,
        slippage_ticks=args.slippage_ticks,
        fee_buy_pct=settings.risk_fee_buy_pct,
        fee_sell_pct=settings.risk_fee_sell_pct,
        warmup_bars=settings.daily_min_history_bars,
    )
    variants = tuple(v for v in DEFAULT_VARIANTS if not args.variants or v.name in args.variants)
    is_cfg = BacktestConfig(start=is_start, end=is_end, **common)
    rows = run_variants(variants, frames, index_frame, rules=rules, risk=risk, cfg=is_cfg)
    print(
        f"IN-SAMPLE {is_start}..{is_end} ({len(frames)} saham; indeks {'ada' if index_frame else 'TIDAK ADA'})"
    )
    print(format_table(rows))
    print("\nPer strategi (trade, expectancy R):")
    for r in rows:
        print(f"  {r.variant.name:22}{r.as_row()['by_strategy']}")
    report: dict[str, object] = {
        "in_sample": {
            "period": [is_start.isoformat(), is_end.isoformat()],
            "rows": [r.as_row() for r in rows],
        },
        "variants": [v.as_dict() for v in variants],
        "universe": sorted(frames),
    }
    if args.oos_for:
        chosen = next((v for v in DEFAULT_VARIANTS if v.name == args.oos_for), None)
        if chosen is None:
            print(f"varian {args.oos_for!r} tidak dikenal", file=sys.stderr)
            return 2
        oos_cfg = BacktestConfig(start=oos_start, end=oos_end, **common)
        [oos_row] = run_variants(
            (chosen,), frames, index_frame, rules=rules, risk=risk, cfg=oos_cfg
        )
        evaluation = evaluate_gate(oos_row.result, thresholds)
        print(
            f"\nOUT-OF-SAMPLE {oos_start}..{oos_end} — varian {chosen.name} (SATU kali evaluasi):"
        )
        print(format_table([oos_row]))
        print(
            "  gate:",
            "LULUS" if evaluation.passed else "TIDAK LULUS",
            "|",
            "; ".join(evaluation.failures) or "semua pemeriksaan lolos",
        )
        report["out_of_sample"] = {
            "variant": chosen.name,
            "row": oos_row.as_row(),
            "gate_passed": evaluation.passed,
            "failures": evaluation.failures,
        }
    out_dir = settings.var_dir / "research"
    out_path = out_dir / f"research_{start.isoformat()}_{end.isoformat()}.json"
    out_path.write_text(_json(report), encoding="utf-8")
    print(f"\nlaporan: {out_path}")
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
    dry = sub.add_parser("dryrun", help="satu job end-to-end tanpa kirim (jaringan)")
    dry.add_argument("which", choices=["morning", "afternoon"])
    sub.add_parser("run", help="jalankan bot Telegram + scheduler")
    sc = sub.add_parser("screen", help="statistik BSJP/BPJS (bukan sinyal)")
    sc.add_argument("--style", choices=["bsjp", "bpjs"], required=True)
    sc.add_argument("--top", type=int, default=5)
    sc.add_argument("--lookback", type=int, default=60)
    sc.add_argument("--symbols", nargs="*")
    rs = sub.add_parser("research", help="grid varian in-sample; OOS sekali untuk varian pilihan")
    rs.add_argument("--start", required=True)
    rs.add_argument("--end", required=True)
    rs.add_argument("--symbols", nargs="*")
    rs.add_argument("--fetch", action="store_true", help="unduh ulang data ke cache riset")
    rs.add_argument("--variants", nargs="*", help="subset nama varian")
    rs.add_argument("--oos-for", help="nama varian yang dievaluasi SEKALI pada out-of-sample")
    rs.add_argument("--slippage-ticks", type=int, default=1)
    rs.add_argument("--oos-fraction", type=float, default=0.3)
    bt = sub.add_parser("backtest", help="backtest engine yang sama; gate out-of-sample")
    bt.add_argument("--start", required=True, help="YYYY-MM-DD")
    bt.add_argument("--end", required=True, help="YYYY-MM-DD")
    bt.add_argument("--symbols", nargs="*", help="override watchlist")
    bt.add_argument("--csv-dir", help="folder CSV per simbol (date,open,high,low,close,volume)")
    bt.add_argument("--slippage-ticks", type=int, default=1)
    bt.add_argument("--min-trades", type=int, default=30)
    bt.add_argument("--min-pf", type=float, default=1.3)
    bt.add_argument("--max-dd", type=float, default=15.0)
    bt.add_argument("--oos-fraction", type=float, default=0.3)
    bt.add_argument(
        "--save-gate",
        action="store_true",
        help="simpan hasil gate ke DB (membuka gate produksi bila lulus)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env_file = None if args.env_file == "-" else args.env_file
    try:
        settings = load_settings(env_file)
    except Exception as exc:  # noqa: BLE001 - tampilkan error konfigurasi dengan jelas
        print(f"Konfigurasi tidak valid:\n{exc}", file=sys.stderr)
        return 2
    configure_logging(
        level=settings.log_level,
        diagnose=settings.log_diagnose,
        log_dir=settings.var_dir / "logs" if args.command == "run" else None,
    )
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
    if args.command == "dryrun":
        return asyncio.run(cmd_dryrun(settings, args.which))
    if args.command == "run":
        return asyncio.run(cmd_run(settings))
    if args.command == "backtest":
        return asyncio.run(cmd_backtest(settings, args))
    if args.command == "screen":
        return asyncio.run(cmd_screen(settings, args))
    if args.command == "research":
        return asyncio.run(cmd_research(settings, args))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
