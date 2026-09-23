"""Formatter: ReportSnapshot → HTML Telegram (escape semua teks dinamis, split deterministik).

Formatter tidak menghitung ulang angka; ia hanya merender string dari snapshot. Pemecahan pesan
dilakukan pada batas blok (kartu sinyal utuh) agar markup tidak terpotong, lalu setiap bagian
diberi footer "(bagian i/n)". Setiap bagian divalidasi tag-balanced dan ≤ 4096 karakter.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from core.snapshot import (
    ActiveSignalSnapshot,
    MoneyFlowSnapshot,
    ReportSnapshot,
    ReportType,
    SignalCardSnapshot,
    SignalUpdateSnapshot,
)
from core.timeutil import to_wib

MAX_LEN = 4096
_FOOTER_RESERVE = 40
_ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler"}
_TAG_RE = re.compile(r"<(/?)([a-z-]+)(\s[^<>]*)?>")

STATUS_LABEL = {
    "pending_entry": "menunggu entry",
    "active": "aktif",
    "closed_tp": "tutup di TP",
    "closed_sl": "tutup di SL",
    "closed_time": "tutup (masa tahan habis)",
    "expired": "kedaluwarsa",
    "cancelled": "dibatalkan",
}
STRATEGY_LABEL = {
    "trend_pullback": "Trend Pullback",
    "breakout": "Breakout",
    "reversal": "Reversal",
    "foreign_flow": "Foreign Flow",
    "smart_money": "Smart Money (Broker Akumulasi)",
    "money_flow_proxy": "Smart Money Proxy (Volume)",
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def fmt_num(value: str | Decimal | None, places: int | None = None) -> str:
    """Format angka gaya Indonesia: pemisah ribuan '.', desimal ','."""
    if value is None:
        return "—"
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return esc(value)
    if places is None:
        d = d.normalize()
        places = max(0, -d.as_tuple().exponent) if d != d.to_integral() else 0
        places = min(places, 4)
    q = Decimal(1).scaleb(-places)
    text = f"{d.quantize(q):,.{places}f}"
    return text.replace(",", "\u0000").replace(".", ",").replace("\u0000", ".")


def fmt_pct(value: str | None) -> str:
    if value is None:
        return "—"
    d = Decimal(value)
    sign = "+" if d > 0 else ""
    return f"{sign}{fmt_num(d, 2)}%"


def fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return to_wib(value).strftime("%d %b %Y %H:%M WIB")


def fmt_date(value) -> str:
    return value.strftime("%d %b %Y")


# ----------------------------------------------------------------------------- blok


def _header(s: ReportSnapshot) -> str:
    origin_tag = "" if s.origin == "live" else f" <i>[{esc(s.origin.upper())}]</i>"
    return (
        f"<b>📊 {esc(s.report_type.title)} — IDX</b>{origin_tag}\n"
        f"Tanggal: <b>{esc(fmt_date(s.trading_date))}</b> · dibuat {esc(fmt_dt(s.generated_at))}\n"
        f"Data sesi: {esc(fmt_date(s.data_session_date))}"
    )


def _data_quality(s: ReportSnapshot) -> str:
    dq = s.data_quality
    levels = ", ".join(f"{esc(k)}: {v}" for k, v in sorted(dq.quality_levels.items())) or "—"
    lines = [
        "<b>🧪 Kualitas data</b>",
        f"Simbol dievaluasi: {dq.symbols_evaluated}/{dq.symbols_total} · kualitas {levels}",
        f"Provider: {esc(', '.join(dq.providers_used) or '—')}",
        f"Aturan bursa: <i>{esc(dq.rules_label)}</i> · Kalender: <i>{esc(dq.calendar_label)}</i>",
    ]
    if dq.blocked:
        shown = dq.blocked[:6]
        lines.append("Dilewati: " + "; ".join(f"{esc(b.symbol)} ({esc(b.reason)})" for b in shown))
        if len(dq.blocked) > len(shown):
            lines.append(f"… dan {len(dq.blocked) - len(shown)} simbol lain")
    for note in dq.notes:
        lines.append(f"• {esc(note)}")
    return "\n".join(lines)


def _global(s: ReportSnapshot) -> str | None:
    g = s.global_context
    if g.status == "unavailable":
        return f"<b>🌏 Global</b>\nTidak tersedia{': ' + esc(g.reason) if g.reason else ''}."
    lines = ["<b>🌏 Global semalam</b>"]
    for item in g.items:
        if item.status == "ok":
            flag = "" if item.verified else " <i>(simbol belum diverifikasi)</i>"
            lines.append(
                f"• {esc(item.label)}: {fmt_num(item.last, 2)} ({esc(fmt_pct(item.change_pct))}){flag}"
            )
        else:
            lines.append(f"• {esc(item.label)}: tidak tersedia")
    if g.status == "partial":
        lines.append("<i>Sebagian instrumen tidak tersedia.</i>")
    return "\n".join(lines)


def _news(s: ReportSnapshot) -> str | None:
    n = s.news
    if n.status == "not_configured":
        return None
    if n.status == "unavailable":
        return "<b>📰 Berita</b>\nTidak tersedia."
    lines = ["<b>📰 Berita terkait</b>"]
    for item in n.items:
        when = f" · {esc(fmt_dt(item.published_at))}" if item.published_at else ""
        lines.append(
            f'• <a href="{html.escape(item.url, quote=True)}">{esc(item.title)}</a> — {esc(item.source)}{when}'
        )
    if n.status == "partial":
        lines.append("<i>Sebagian sumber gagal diambil.</i>")
    return "\n".join(lines)


def _narrative(s: ReportSnapshot) -> str | None:
    if not s.narrative:
        return None
    return f"<b>📝 Ringkasan</b>\n{esc(s.narrative)}"


def _card(c: SignalCardSnapshot, index: int) -> str:
    strategy = STRATEGY_LABEL.get(c.strategy, c.strategy)
    lines = [
        f"<b>#{index} {esc(c.symbol)}</b> · {esc(strategy)} v{esc(c.strategy_version)} · confidence <b>{c.confidence}</b>/100",
        f"Entry: <b>{fmt_num(c.entry_low)}–{fmt_num(c.entry_high)}</b> · berlaku {c.entry_valid_sessions} sesi",
        f"SL: <b>{fmt_num(c.stop_loss)}</b> · TP1/TP2/TP3: <b>{fmt_num(c.tp1)}</b> / {fmt_num(c.tp2)} / {fmt_num(c.tp3)}",
        f"R:R TP1: {fmt_num(c.rr_tp1_gross, 2)} (bersih {fmt_num(c.rr_tp1_net, 2)}) · ATR {fmt_num(c.atr)} · ARA/ARB {fmt_num(c.ara)}/{fmt_num(c.arb)}",
    ]
    if c.sizing is not None:
        sz = c.sizing
        if sz.lots > 0:
            lines.append(
                f"Sizing contoh (modal {fmt_num(sz.capital_example, 0)}, risiko {fmt_num(sz.risk_pct)}%): "
                f"<b>{sz.lots} lot</b> ≈ Rp{fmt_num(sz.notional, 0)}"
            )
        else:
            lines.append(f"Sizing contoh: <b>0 lot</b> — {esc(sz.note)}")
        if sz.note and sz.lots > 0:
            lines.append(f"<i>{esc(sz.note)}</i>")
    lines.append("Alasan: " + "; ".join(esc(r) for r in c.reasons))
    for note in c.risk_notes:
        lines.append(f"⚠️ {esc(note)}")
    lines.append(
        f"<i>Data {esc(c.provider)} · bar {esc(fmt_dt(c.bar_time))} · kualitas {esc(c.quality)}</i>"
    )
    return "\n".join(lines)


def _signals(s: ReportSnapshot) -> list[str]:
    if not s.signals:
        if s.report_type is ReportType.AFTERNOON:
            note = "Tidak ada setup baru pada laporan sore."
        elif s.data_quality.symbols_evaluated:
            note = "Tidak ada setup layak pada sesi ini."
        else:
            note = "Tidak ada data yang dapat dievaluasi."
        return [f"<b>🎯 Setup</b>\n{esc(note)}"]
    blocks = [f"<b>🎯 Setup ({len(s.signals)})</b>"]
    blocks.extend(_card(c, i + 1) for i, c in enumerate(s.signals))
    return blocks


def _update_line(u: SignalUpdateSnapshot) -> str:
    price = f" @ {fmt_num(u.trigger_price)}" if u.trigger_price else ""
    pnl = f" · {fmt_num(u.pnl_r, 2)}R" if u.pnl_r is not None else ""
    return f"• {esc(u.symbol)}: {esc(STATUS_LABEL.get(u.previous_status, u.previous_status))} → <b>{esc(STATUS_LABEL.get(u.new_status, u.new_status))}</b>{price}{pnl}"


def _active_line(a: ActiveSignalSnapshot) -> str:
    fill = f" · isi {fmt_num(a.filled_price)}" if a.filled_price else ""
    last = f" · terakhir {fmt_num(a.last_price)}" if a.last_price else ""
    return (
        f"• {esc(a.symbol)} ({esc(STRATEGY_LABEL.get(a.strategy, a.strategy))}) — "
        f"<b>{esc(STATUS_LABEL.get(a.status, a.status))}</b> sejak {esc(fmt_date(a.published_session))} · "
        f"entry {fmt_num(a.entry_low)}–{fmt_num(a.entry_high)} · SL {fmt_num(a.stop_loss)} · TP1 {fmt_num(a.tp1)}{fill}{last}"
    )


def _money_flow_line(m: MoneyFlowSnapshot) -> str:
    arrow = "▲" if m.label == "akumulasi" else "▼"
    quiet = " · harga tenang" if m.quiet and m.label == "akumulasi" else ""
    return (
        f"{arrow} <b>{esc(m.symbol)}</b> skor {m.score:+d} · CMF {fmt_num(m.cmf20, 2)} · "
        f"OBV {fmt_num(m.obv_slope_days, 1)} hari vol · akum/dist {m.acc_days}/{m.dist_days} · "
        f"Δ{esc(fmt_pct(m.price_change_pct))}{quiet}"
    )


def _money_flow(s: ReportSnapshot) -> str | None:
    if not s.money_flow:
        return None
    lines = ["<b>💰 Money Flow</b> (proxy volume, data harga Yahoo — bukan data broker)"]
    acc = [m for m in s.money_flow if m.label == "akumulasi"]
    dist = [m for m in s.money_flow if m.label == "distribusi"]
    if acc:
        lines.append("Akumulasi:")
        lines.extend(_money_flow_line(m) for m in acc)
    if dist:
        lines.append("Distribusi:")
        lines.extend(_money_flow_line(m) for m in dist)
    lines.append(
        "<i>Skor −100…+100 dari CMF20, OBV, hari akumulasi/distribusi, rasio volume; bukan sinyal.</i>"
    )
    return "\n".join(lines)


def _recap(s: ReportSnapshot) -> str | None:
    if not s.active_updates and not s.active_signals:
        return None
    lines = ["<b>🔁 Sinyal berjalan</b>"]
    if s.active_updates:
        lines.append("Update:")
        lines.extend(_update_line(u) for u in s.active_updates)
    if s.active_signals:
        lines.append("Posisi simulasi terbuka:")
        lines.extend(_active_line(a) for a in s.active_signals)
    lines.append("<i>Status adalah simulasi sinyal, bukan transaksi Anda.</i>")
    return "\n".join(lines)


def _disclaimer(s: ReportSnapshot) -> str:
    return f"<i>⚠️ {esc(s.disclaimer)}</i>"


def report_blocks(s: ReportSnapshot) -> list[str]:
    blocks: list[str] = [_header(s), _data_quality(s)]
    for maybe in (_global(s), _news(s), _narrative(s)):
        if maybe:
            blocks.append(maybe)
    if s.market_bias:
        label = {"bullish": "naik", "neutral": "netral", "bearish": "turun"}.get(
            s.market_bias, s.market_bias
        )
        detail = f" — {esc(s.regime_detail)}" if s.regime_detail else ""
        blocks.append(f"<b>📈 Rezim pasar</b>: {esc(label)} ({esc(s.market_bias)}){detail}")
    blocks.extend(_signals(s))
    money = _money_flow(s)
    if money:
        blocks.append(money)
    recap = _recap(s)
    if recap:
        blocks.append(recap)
    for note in s.engine_notes:
        blocks.append(f"<i>{esc(note)}</i>")
    blocks.append(_disclaimer(s))
    return blocks


# ----------------------------------------------------------------------------- split & validasi


def validate_html(text: str) -> None:
    """Pastikan hanya tag yang didukung Telegram, seimbang, dan tidak ada '<' liar."""
    stack: list[str] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        between = text[pos : m.start()]
        if "<" in between:
            raise ValueError("karakter '<' tanpa escape")
        pos = m.end()
        closing, name = m.group(1) == "/", m.group(2)
        if name not in _ALLOWED_TAGS:
            raise ValueError(f"tag tidak didukung: <{name}>")
        if closing:
            if not stack or stack[-1] != name:
                raise ValueError(f"tag penutup tidak seimbang: </{name}>")
            stack.pop()
        else:
            stack.append(name)
    if "<" in text[pos:]:
        raise ValueError("karakter '<' tanpa escape di akhir")
    if stack:
        raise ValueError(f"tag belum ditutup: {stack}")


def _hard_split(block: str, limit: int) -> list[str]:
    """Blok tunggal yang terlalu panjang dipecah per baris (tanpa memotong tag di tengah)."""
    parts: list[str] = []
    current = ""
    for line in block.split("\n"):
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > limit:
            if current:
                parts.append(current)
            current = line[:limit]
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def split_blocks(blocks: list[str], *, limit: int = MAX_LEN) -> list[str]:
    body_limit = limit - _FOOTER_RESERVE
    pages: list[str] = []
    current = ""
    for block in blocks:
        pieces = [block] if len(block) <= body_limit else _hard_split(block, body_limit)
        for piece in pieces:
            candidate = piece if not current else f"{current}\n\n{piece}"
            if len(candidate) > body_limit and current:
                pages.append(current)
                current = piece
            else:
                current = candidate
    if current:
        pages.append(current)
    if len(pages) > 1:
        pages = [f"{p}\n\n<i>(bagian {i + 1}/{len(pages)})</i>" for i, p in enumerate(pages)]
    for page in pages:
        validate_html(page)
        if len(page) > limit:
            raise ValueError("bagian melebihi batas panjang Telegram")
    return pages


def format_report(snapshot: ReportSnapshot, *, limit: int = MAX_LEN) -> list[str]:
    return split_blocks(report_blocks(snapshot), limit=limit)


def format_teaser(text: str, s: ReportSnapshot) -> str:
    """Pesan pembuka (hype) sebelum laporan; teks dari konfigurasi di-escape, tanpa angka trading."""
    title = s.report_type.title
    return (
        f"<b>{esc(text.strip())}</b>\n"
        f"{esc(title)} {esc(fmt_date(s.trading_date))} menyusul sebentar lagi — "
        f"{len(s.signals)} setup dari engine."
        + ("" if s.origin == "live" else f" <i>[{esc(s.origin.upper())}]</i>")
    )


def format_test_message() -> str:
    return "TEST — BUKAN SINYAL TRADING.\nUji pengiriman bot saham IDX ke grup berhasil."
