from __future__ import annotations

import re
from datetime import UTC, date, datetime

from bot.formatter import fmt_num, format_report
from core.snapshot import (
    ActiveSignalSnapshot,
    GlobalContextSnapshot,
    MacroItemSnapshot,
    NewsSnapshot,
    ReportType,
    SnapshotOrigin,
    build_snapshot,
)
from data.providers.base import QualityStatus
from engine.pipeline import SignalEngine, SymbolInput
from notifications.whatsapp_export import export_whatsapp, render_whatsapp
from tests.engine_fixtures import END_SESSION, trend_pullback_frame

NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)


def _snapshot(market_rules):
    engine = SignalEngine(market_rules)
    result = engine.run(
        END_SESSION, {"TRND": SymbolInput(trend_pullback_frame(), QualityStatus.DEGRADED)}
    )
    return build_snapshot(
        report_type=ReportType.MORNING,
        origin=SnapshotOrigin.DRY_RUN,
        trading_date=date(2026, 3, 16),
        generated_at=NOW,
        engine_result=result,
        strategy_versions=engine.strategy_versions,
        quality_by_symbol={"TRND": QualityStatus.DEGRADED},
        providers_used=("fixture",),
        data_blocked=(),
        rules_label="CONTOH / BELUM TERVERIFIKASI",
        calendar_label="CONTOH / BELUM TERVERIFIKASI",
        global_context=GlobalContextSnapshot(
            status="ok",
            fetched_at=NOW,
            items=(
                MacroItemSnapshot(
                    id="sp500",
                    label="S&P 500",
                    unit="poin",
                    status="ok",
                    verified=False,
                    last="7706.0298",
                    change_pct="-0.76",
                    as_of=NOW,
                ),
            ),
        ),
        news=NewsSnapshot(status="not_configured"),
        entry_valid_sessions=3,
        active_signals=(
            ActiveSignalSnapshot(
                symbol="ABCD",
                strategy="breakout",
                status="active",
                published_session=date(2026, 3, 12),
                entry_low="1000",
                entry_high="1010",
                stop_loss="960",
                tp1="1110",
                filled_price="1005",
            ),
        ),
    ).model_copy(update={"narrative": "Ringkasan dengan *bintang* dan _garis bawah_."})


def test_whatsapp_text_matches_telegram_numbers_and_uses_whatsapp_markup(market_rules) -> None:
    snap = _snapshot(market_rules)
    text = render_whatsapp(snap)
    telegram = "\n".join(format_report(snap))
    assert "<" not in text.replace("<–", "") or not re.search(r"<[a-z]", text)  # tanpa tag HTML
    assert text.startswith("*📊 PRE-MARKET BRIEF — IDX* [DRY_RUN]")
    card = snap.signals[0]
    for value in (card.entry_low, card.entry_high, card.stop_loss, card.tp1, card.tp2, card.tp3):
        assert fmt_num(value) in text and fmt_num(value) in telegram
    assert f"confidence {card.confidence}/100" in text
    assert "berlaku 3 sesi" in text
    assert snap.disclaimer in text and snap.disclaimer in telegram
    assert "16 Mar 2026 08:00 WIB" in text and "16 Mar 2026 08:00 WIB" in telegram
    assert "S&P 500: 7.706,03 (-0,76%)" in text
    assert "ABCD" in text and "aktif" in text
    # markup berpasangan dari teks bebas dinetralkan agar tidak merusak format WhatsApp;
    # underscore tunggal (nama berkas) dibiarkan
    assert "*bintang*" not in text and "_garis bawah_" not in text and "bintang" in text
    notes_snap = snap.model_copy(
        update={
            "data_quality": snap.data_quality.model_copy(
                update={"notes": ("market_rules.yaml belum terverifikasi",)}
            )
        }
    )
    assert "market_rules.yaml" in render_whatsapp(notes_snap)
    assert "CONTOH / BELUM TERVERIFIKASI" in text


def test_export_writes_local_file_per_origin(market_rules, tmp_path) -> None:
    snap = _snapshot(market_rules)
    path = export_whatsapp(snap, tmp_path)
    assert path == tmp_path / "whatsapp" / "dry_run" / "2026-03-16_morning.whatsapp.txt"
    assert path.read_text(encoding="utf-8") == render_whatsapp(snap)
    assert not any(p.suffix == ".html" for p in path.parent.iterdir())


def test_afternoon_and_empty_variants(market_rules) -> None:
    snap = _snapshot(market_rules).model_copy(
        update={"signals": (), "report_type": ReportType.AFTERNOON, "narrative": None}
    )
    text = render_whatsapp(snap)
    assert "PRE-CLOSE SIGNAL" in text and "tidak ada setup baru pada laporan sore" in text
    assert "📝" not in text
