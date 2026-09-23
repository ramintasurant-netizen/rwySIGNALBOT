from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from bot.formatter import (
    MAX_LEN,
    esc,
    fmt_num,
    fmt_pct,
    format_report,
    format_test_message,
    report_blocks,
    split_blocks,
    validate_html,
)
from core.snapshot import (
    ActiveSignalSnapshot,
    BlockedInfo,
    GlobalContextSnapshot,
    MacroItemSnapshot,
    NewsItemSnapshot,
    NewsSnapshot,
    ReportType,
    SignalUpdateSnapshot,
    SnapshotOrigin,
    build_snapshot,
)
from data.providers.base import QualityStatus
from engine.pipeline import SignalEngine, SymbolInput
from tests.engine_fixtures import END_SESSION, breakout_frame, reversal_frame, trend_pullback_frame

NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)


def _snapshot(market_rules, *, with_news=True, narrative=None):
    engine = SignalEngine(market_rules)
    universe = {
        "TRND": SymbolInput(trend_pullback_frame(), QualityStatus.DEGRADED),
        "BRKO": SymbolInput(breakout_frame(), QualityStatus.DEGRADED),
        "RVSL": SymbolInput(reversal_frame(), QualityStatus.DEGRADED),
    }
    result = engine.run(END_SESSION, universe)
    news = (
        NewsSnapshot(
            status="ok",
            items=(
                NewsItemSnapshot(
                    source="Media <Uji> & Co",
                    title='Judul dengan <b>tag</b> & "kutip" — <script>alert(1)</script>',
                    url="https://example.com/a?x=1&y=2",
                    published_at=NOW,
                ),
            ),
        )
        if with_news
        else NewsSnapshot(status="not_configured")
    )
    return build_snapshot(
        report_type=ReportType.MORNING,
        origin=SnapshotOrigin.DRY_RUN,
        trading_date=date(2026, 3, 16),
        generated_at=NOW,
        engine_result=result,
        strategy_versions=engine.strategy_versions,
        quality_by_symbol={k: QualityStatus.DEGRADED for k in universe},
        providers_used=("fixture",),
        data_blocked=(
            BlockedInfo(symbol="XXXX", stage="data", reason="missing: <provider> gagal"),
        ),
        rules_label="CONTOH / BELUM TERVERIFIKASI",
        calendar_label="CONTOH / BELUM TERVERIFIKASI",
        global_context=GlobalContextSnapshot(
            status="partial",
            fetched_at=NOW,
            items=(
                MacroItemSnapshot(
                    id="sp500",
                    label="S&P 500",
                    unit="poin",
                    status="ok",
                    verified=False,
                    last="5100.25",
                    change_pct="0.99",
                    as_of=NOW,
                ),
                MacroItemSnapshot(
                    id="coal",
                    label="Batu bara",
                    unit="USD/ton",
                    status="unavailable",
                    verified=False,
                    reason="belum ada sumber",
                ),
            ),
        ),
        news=news,
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
        active_updates=(
            SignalUpdateSnapshot(
                symbol="EFGH",
                previous_status="active",
                new_status="closed_tp",
                trigger_price="1500",
                trigger_session=date(2026, 3, 13),
                pnl_r="2.33",
            ),
        ),
    ).model_copy(update={"narrative": narrative})


def test_fmt_num_indonesian_style() -> None:
    assert fmt_num("1234567") == "1.234.567"
    assert fmt_num("1234.5") == "1.234,5"
    assert fmt_num(Decimal("2.3300"), 2) == "2,33"
    assert fmt_num("1000.0000") == "1.000"
    assert fmt_num(None) == "—"
    assert fmt_pct("-1.5") == "-1,50%" and fmt_pct("0.99") == "+0,99%"


def test_report_escapes_untrusted_text_and_numbers_come_from_snapshot(market_rules) -> None:
    snap = _snapshot(market_rules)
    parts = format_report(snap)
    joined = "\n".join(parts)
    assert "<script>" not in joined and "&lt;script&gt;" in joined
    assert "Media &lt;Uji&gt; &amp; Co" in joined
    assert 'href="https://example.com/a?x=1&amp;y=2"' in joined
    for card in snap.signals:
        assert (
            fmt_num(card.entry_high) in joined
            and fmt_num(card.stop_loss) in joined
            and fmt_num(card.tp1) in joined
        )
        assert f"confidence <b>{card.confidence}</b>" in joined
    assert "CONTOH / BELUM TERVERIFIKASI" in joined
    assert "[DRY_RUN]" in joined
    assert "Bukan ajakan jual/beli" in joined
    assert "EFGH" in joined and "2,33R" in joined and "ABCD" in joined
    assert "simbol belum diverifikasi" in joined  # label makro
    for part in parts:
        validate_html(part)
        assert len(part) <= MAX_LEN


def test_no_setup_message_and_narrative(market_rules) -> None:
    snap = _snapshot(market_rules, with_news=False, narrative="Ringkasan <aman> & jelas.")
    empty = snap.model_copy(update={"signals": ()})
    joined = "\n".join(format_report(empty))
    assert "Tidak ada setup layak" in joined
    assert "📰" not in joined  # not_configured → blok berita dihilangkan
    assert "Ringkasan &lt;aman&gt; &amp; jelas." in joined


def test_split_is_deterministic_and_keeps_blocks_intact(market_rules) -> None:
    snap = _snapshot(market_rules)
    blocks = report_blocks(snap)
    parts = split_blocks(blocks, limit=900)
    assert len(parts) > 1
    assert parts == split_blocks(blocks, limit=900)
    for i, part in enumerate(parts, 1):
        validate_html(part)
        assert len(part) <= 900
        assert f"(bagian {i}/{len(parts)})" in part
    # setiap kartu utuh dalam satu bagian
    for card in snap.signals:
        assert sum(1 for p in parts if "<b>#" in p and card.symbol in p) >= 1


def test_oversized_single_block_is_hard_split_without_breaking_tags() -> None:
    block = "\n".join(f"<b>baris {i}</b> teks panjang " + "x" * 50 for i in range(60))
    parts = split_blocks([block], limit=600)
    assert len(parts) > 1
    for p in parts:
        validate_html(p)
        assert len(p) <= 600


@pytest.mark.parametrize(
    "bad",
    [
        "<b>tidak ditutup",
        "<i>silang<b></i></b>",
        "1 < 2 tanpa escape",
        "<div>tidak didukung</div>",
        "</b> penutup liar",
    ],
)
def test_validate_html_rejects_bad_markup(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_html(bad)


def test_validate_html_accepts_supported_markup() -> None:
    validate_html(
        '<b>a</b> <i>b</i> <code>c</code> <a href="https://x.y/?a=1&amp;b=2">d</a> &lt; &amp;'
    )


def test_esc_and_test_message() -> None:
    assert esc("<&>") == "&lt;&amp;&gt;"
    msg = format_test_message()
    assert "BUKAN SINYAL TRADING" in msg and "entry" not in msg.lower()
