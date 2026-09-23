"""Ekspor teks siap salin untuk SALURAN WhatsApp (manual, tanpa otomasi).

- Sumber: ``ReportSnapshot`` yang SAMA dengan Telegram; angka/timestamp/disclaimer identik maknanya.
- Format teks WhatsApp (``*tebal*``, ``_miring_``), bukan HTML.
- Disimpan lokal ke ``var/exports/whatsapp/``; tidak pernah diunggah otomatis ke pihak mana pun.
- Otomasi (API resmi) belum tersedia dan TIDAK disimulasikan (docs/verification_required.md §9).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from bot.formatter import STATUS_LABEL, STRATEGY_LABEL, fmt_dt, fmt_num, fmt_pct
from core.snapshot import ReportSnapshot, ReportType, SignalCardSnapshot

WHATSAPP_MAX_CHARS = 65_536  # batas praktis; dicek agar ekspor tidak terpotong diam-diam


_WA_PAIRS = (re.compile(r"\*([^*\n]+)\*"), re.compile(r"_([^_\n]+)_"), re.compile(r"~([^~\n]+)~"))
_ZWSP = "\u200b"


def _wa_safe(text: str) -> str:
    """Netralkan markup WhatsApp BERPASANGAN dalam teks bebas (mis. *tebal*, _miring_) agar tidak
    mengubah format; underscore tunggal seperti pada nama berkas dibiarkan."""
    out = text.replace("```", "'''")
    for pattern in _WA_PAIRS:
        out = pattern.sub(
            lambda m: f"{m.group(0)[0]}{_ZWSP}{m.group(1)}{_ZWSP}{m.group(0)[-1]}", out
        )
    return out


def _date(d: date) -> str:
    return d.strftime("%d %b %Y")


def _card(c: SignalCardSnapshot, index: int) -> list[str]:
    lines = [
        f"*#{index} {c.symbol}* · {STRATEGY_LABEL.get(c.strategy, c.strategy)} · confidence {c.confidence}/100",
        f"Entry: *{fmt_num(c.entry_low)}–{fmt_num(c.entry_high)}* (berlaku {c.entry_valid_sessions} sesi)",
        f"SL: *{fmt_num(c.stop_loss)}* · TP1/TP2/TP3: *{fmt_num(c.tp1)}* / {fmt_num(c.tp2)} / {fmt_num(c.tp3)}",
        f"R:R TP1: {fmt_num(c.rr_tp1_gross, 2)} (bersih {fmt_num(c.rr_tp1_net, 2)}) · ATR {fmt_num(c.atr)}",
    ]
    if c.sizing is not None:
        if c.sizing.lots > 0:
            lines.append(
                f"Sizing contoh (modal {fmt_num(c.sizing.capital_example, 0)}, risiko {fmt_num(c.sizing.risk_pct)}%): "
                f"*{c.sizing.lots} lot* ≈ Rp{fmt_num(c.sizing.notional, 0)}"
            )
        else:
            lines.append(f"Sizing contoh: *0 lot* — {_wa_safe(c.sizing.note)}")
    lines.append("Alasan: " + "; ".join(_wa_safe(r) for r in c.reasons))
    for note in c.risk_notes:
        lines.append(f"⚠️ {_wa_safe(note)}")
    lines.append(f"_Data {_wa_safe(c.provider)} · bar {fmt_dt(c.bar_time)} · kualitas {c.quality}_")
    return lines


def render_whatsapp(s: ReportSnapshot) -> str:
    origin_tag = "" if s.origin == "live" else f" [{s.origin.upper()}]"
    out: list[str] = [
        f"*📊 {s.report_type.title} — IDX*{origin_tag}",
        f"Tanggal: *{_date(s.trading_date)}* · dibuat {fmt_dt(s.generated_at)}",
        f"Data sesi: {_date(s.data_session_date)}",
        "",
        "*🧪 Kualitas data*",
        f"Simbol dievaluasi: {s.data_quality.symbols_evaluated}/{s.data_quality.symbols_total}",
        f"Aturan bursa: _{_wa_safe(s.data_quality.rules_label)}_ · Kalender: _{_wa_safe(s.data_quality.calendar_label)}_",
    ]
    if s.data_quality.blocked:
        out.append(
            "Dilewati: "
            + "; ".join(f"{b.symbol} ({_wa_safe(b.reason)})" for b in s.data_quality.blocked[:6])
        )
    for note in s.data_quality.notes:
        out.append(f"• {_wa_safe(note)}")

    g = s.global_context
    out.append("")
    if g.status == "unavailable":
        out.append("*🌏 Global*: tidak tersedia" + (f" ({_wa_safe(g.reason)})" if g.reason else ""))
    else:
        out.append("*🌏 Global semalam*")
        for item in g.items:
            if item.status == "ok":
                flag = "" if item.verified else " _(simbol belum diverifikasi)_"
                out.append(
                    f"• {_wa_safe(item.label)}: {fmt_num(item.last, 2)} ({fmt_pct(item.change_pct)}){flag}"
                )
            else:
                out.append(f"• {_wa_safe(item.label)}: tidak tersedia")

    if s.news.status not in ("not_configured",):
        out.append("")
        if s.news.status == "unavailable":
            out.append("*📰 Berita*: tidak tersedia")
        else:
            out.append("*📰 Berita terkait*")
            for item in s.news.items:
                out.append(f"• {_wa_safe(item.title)} — {_wa_safe(item.source)}\n  {item.url}")

    if s.narrative:
        out += ["", "*📝 Ringkasan*", _wa_safe(s.narrative)]

    out.append("")
    if s.signals:
        out.append(f"*🎯 Setup ({len(s.signals)})*")
        for i, c in enumerate(s.signals, 1):
            out.append("")
            out.extend(_card(c, i))
    else:
        if s.report_type is ReportType.AFTERNOON:
            out.append("*🎯 Setup*: tidak ada setup baru pada laporan sore.")
        elif s.data_quality.symbols_evaluated:
            out.append("*🎯 Setup*: tidak ada setup layak pada sesi ini.")
        else:
            out.append("*🎯 Setup*: tidak ada data yang dapat dievaluasi.")

    if s.active_updates or s.active_signals:
        out += ["", "*🔁 Sinyal berjalan*"]
        for u in s.active_updates:
            price = f" @ {fmt_num(u.trigger_price)}" if u.trigger_price else ""
            pnl = f" · {fmt_num(u.pnl_r, 2)}R" if u.pnl_r is not None else ""
            out.append(
                f"• {u.symbol}: {STATUS_LABEL.get(u.previous_status, u.previous_status)} → "
                f"*{STATUS_LABEL.get(u.new_status, u.new_status)}*{price}{pnl}"
            )
        for a in s.active_signals:
            fill = f" · isi {fmt_num(a.filled_price)}" if a.filled_price else ""
            out.append(
                f"• {a.symbol} ({STRATEGY_LABEL.get(a.strategy, a.strategy)}) — *{STATUS_LABEL.get(a.status, a.status)}* "
                f"sejak {_date(a.published_session)} · entry {fmt_num(a.entry_low)}–{fmt_num(a.entry_high)} · "
                f"SL {fmt_num(a.stop_loss)} · TP1 {fmt_num(a.tp1)}{fill}"
            )
        out.append("_Status adalah simulasi sinyal, bukan transaksi Anda._")

    for note in s.engine_notes:
        out += ["", f"_{_wa_safe(note)}_"]
    out += ["", f"_⚠️ {s.disclaimer}_"]
    text = "\n".join(out)
    if len(text) > WHATSAPP_MAX_CHARS:
        raise ValueError(
            f"ekspor WhatsApp {len(text)} karakter melebihi batas {WHATSAPP_MAX_CHARS}"
        )
    return text


def export_whatsapp(snapshot: ReportSnapshot, export_dir: Path) -> Path:
    folder = export_dir / "whatsapp" / snapshot.origin.value
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{snapshot.trading_date.isoformat()}_{snapshot.report_type.value}.whatsapp.txt"
    path.write_text(render_whatsapp(snapshot), encoding="utf-8")
    return path
