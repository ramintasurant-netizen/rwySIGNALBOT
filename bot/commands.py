"""Router command: otorisasi grup/admin + logika command, independen dari python-telegram-bot.

Aturan:
- Pesan dari chat ``private`` atau chat di luar allowlist → diabaikan TANPA balasan.
- Command admin hanya di grup admin (TELEGRAM_ADMIN_CHAT_ID) oleh user_id dalam whitelist,
  dengan identitas pengguna yang dapat diverifikasi (pengirim anonim/sender_chat ditolak).
- Command mahal (/cek, /performance) memakai cooldown per chat.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from loguru import logger

from bot.formatter import (
    STATUS_LABEL,
    STRATEGY_LABEL,
    esc,
    fmt_date,
    fmt_dt,
    fmt_num,
    format_report,
)
from bot.reports import JobOutcome, ReportService
from config.common import canonical_symbol
from config.settings import Settings
from core.snapshot import DISCLAIMER, ReportSnapshot, ReportType, SnapshotOrigin
from core.timeutil import to_wib, utc_now
from data.aggregator import MarketDataAggregator
from data.providers.base import Timeframe
from engine.pipeline import SignalEngine, SymbolInput
from storage.repository import Repository

MEMBER_COMMANDS = (
    "start",
    "help",
    "disclaimer",
    "signal",
    "status",
    "performance",
    "cek",
    "watchlist",
)
ADMIN_COMMANDS = ("broadcast", "pause", "resume", "runnow", "health")
EXPENSIVE_COMMANDS = ("cek", "performance")
ANONYMOUS_ADMIN_BOT_ID = 1087968824  # GroupAnonymousBot


@dataclass(frozen=True, slots=True)
class IncomingCommand:
    chat_id: int
    chat_type: str  # private | group | supergroup | channel
    command: str
    args: tuple[str, ...] = ()
    user_id: int | None = None
    user_is_bot: bool = False
    sender_chat_id: int | None = None  # ada ⇒ pengirim anonim / posting channel
    thread_id: int | None = None


@dataclass(frozen=True, slots=True)
class Reply:
    chat_id: int
    parts: tuple[str, ...]  # HTML
    thread_id: int | None = None


@dataclass
class CommandDependencies:
    settings: Settings
    repo: Repository
    service: ReportService
    aggregator: MarketDataAggregator | None = None
    engine: SignalEngine | None = None
    broadcast: Callable[[str], object] | None = None  # async (html) -> summary str
    clock: Callable[[], datetime] = utc_now
    monotonic: Callable[[], float] = time.monotonic
    cooldowns: dict[tuple[int, str], float] = field(default_factory=dict)


class CommandRouter:
    def __init__(self, deps: CommandDependencies) -> None:
        self.d = deps

    # ------------------------------------------------------------------ otorisasi
    def chat_allowed(self, cmd: IncomingCommand) -> bool:
        if cmd.chat_type == "private" or cmd.chat_type == "sender":
            return False
        return cmd.chat_id in self.d.settings.allowed_chat_ids

    def is_admin(self, cmd: IncomingCommand) -> tuple[bool, str]:
        s = self.d.settings
        if s.admin_target is None:
            return False, "TELEGRAM_ADMIN_CHAT_ID belum dikonfigurasi: command admin dinonaktifkan"
        if cmd.chat_id != s.admin_target.chat_id:
            return False, "command admin hanya diterima di grup admin"
        if (
            cmd.sender_chat_id is not None
            or cmd.user_id is None
            or cmd.user_is_bot
            or cmd.user_id == ANONYMOUS_ADMIN_BOT_ID
        ):
            return False, "identitas pengguna tidak dapat diverifikasi (pengirim anonim/bot)"
        if cmd.user_id not in s.admin_user_ids:
            return False, "user_id tidak ada dalam whitelist admin"
        return True, ""

    def _cooldown_remaining(self, cmd: IncomingCommand) -> float:
        key = (cmd.chat_id, cmd.command)
        window = float(self.d.settings.telegram_command_cooldown_seconds)
        last = self.d.cooldowns.get(key)
        now = self.d.monotonic()
        if last is not None and now - last < window:
            return window - (now - last)
        self.d.cooldowns[key] = now
        return 0.0

    # ------------------------------------------------------------------ dispatch
    async def handle(self, cmd: IncomingCommand) -> Reply | None:
        if not self.chat_allowed(cmd):
            logger.debug(
                "command {} dari chat {} ({}) diabaikan", cmd.command, cmd.chat_id, cmd.chat_type
            )
            return None
        command = cmd.command.lower()
        if command in ADMIN_COMMANDS or (
            command == "watchlist" and cmd.args and cmd.args[0].lower() in ("add", "remove")
        ):
            ok, reason = self.is_admin(cmd)
            if not ok:
                logger.warning(
                    "command admin /{} ditolak dari chat {} user {}: {}",
                    command,
                    cmd.chat_id,
                    cmd.user_id,
                    reason,
                )
                return self._reply(cmd, f"⛔ Ditolak: {esc(reason)}.")
        if command in EXPENSIVE_COMMANDS:
            remaining = self._cooldown_remaining(cmd)
            if remaining > 0:
                return self._reply(
                    cmd, f"⏳ Tunggu {int(remaining) + 1} detik sebelum /{esc(command)} berikutnya."
                )
        handler = getattr(self, f"cmd_{command}", None)
        if handler is None:
            return self._reply(cmd, "Perintah tidak dikenal. Ketik /help.")
        parts = await handler(cmd)
        if parts is None:
            return None
        return (
            self._reply(cmd, *parts) if isinstance(parts, tuple | list) else self._reply(cmd, parts)
        )

    def _reply(self, cmd: IncomingCommand, *parts: str) -> Reply:
        return Reply(cmd.chat_id, tuple(parts), cmd.thread_id)

    @property
    def _origin(self) -> str:
        return (
            SnapshotOrigin.LIVE.value
            if self.d.settings.app_mode == "live"
            else SnapshotOrigin.DRY_RUN.value
        )

    # ------------------------------------------------------------------ anggota
    async def cmd_start(self, cmd: IncomingCommand) -> str:
        return (
            "<b>Bot Sinyal Saham IDX</b>\n"
            "Laporan otomatis 08:30 WIB (pre-market) dan 15:00 WIB (pre-close) dikirim ke grup ini.\n"
            "Ketik /help untuk daftar perintah.\n\n"
            f"<i>{esc(DISCLAIMER)}</i>"
        )

    async def cmd_help(self, cmd: IncomingCommand) -> str:
        lines = [
            "<b>Perintah anggota</b>",
            "/signal — laporan terakhir",
            "/status — sinyal simulasi yang masih terbuka",
            "/performance [7d|30d|all] — statistik simulasi",
            "/cek KODE — evaluasi engine untuk satu saham (bukan sinyal produksi)",
            "/watchlist list — daftar watchlist",
            "/disclaimer",
        ]
        if self.is_admin(cmd)[0]:
            lines += [
                "",
                "<b>Perintah admin</b>",
                "/watchlist add KODE · /watchlist remove KODE",
                "/pause · /resume",
                "/runnow morning|afternoon",
                "/broadcast TEKS",
                "/health",
            ]
        return "\n".join(lines)

    async def cmd_disclaimer(self, cmd: IncomingCommand) -> str:
        return f"<i>{esc(DISCLAIMER)}</i>"

    async def cmd_signal(self, cmd: IncomingCommand) -> tuple[str, ...] | str:
        job = await self.d.repo.latest_completed_job(None, self._origin)
        if job is None or not job.snapshot_json:
            return "Belum ada laporan yang selesai."
        snapshot = ReportSnapshot.from_json(job.snapshot_json)
        return tuple(format_report(snapshot))

    async def cmd_status(self, cmd: IncomingCommand) -> str:
        rows = await self.d.repo.open_signals(self._origin)
        if not rows:
            return "Tidak ada sinyal simulasi yang terbuka."
        lines = ["<b>Sinyal simulasi terbuka</b>"]
        for r in rows:
            lines.append(
                f"• {esc(r.symbol)} ({esc(STRATEGY_LABEL.get(r.strategy, r.strategy))}) — "
                f"<b>{esc(STATUS_LABEL.get(r.status, r.status))}</b> sejak {esc(fmt_date(r.published_session))} · "
                f"entry {fmt_num(r.entry_low)}–{fmt_num(r.entry_high)} · SL {fmt_num(r.stop_loss)} · TP1 {fmt_num(r.tp1)}"
                + (f" · isi {fmt_num(r.filled_price)}" if r.filled_price is not None else "")
            )
        lines.append(f"<i>{esc(DISCLAIMER)}</i>")
        return "\n".join(lines)

    async def cmd_performance(self, cmd: IncomingCommand) -> str:
        window = cmd.args[0].lower() if cmd.args else "30d"
        today = to_wib(self.d.clock()).date()
        since: date | None
        if window == "7d":
            since = today - timedelta(days=7)
        elif window == "30d":
            since = today - timedelta(days=30)
        elif window == "all":
            since = None
        else:
            return "Format: /performance [7d|30d|all]"
        p = await self.d.repo.performance(self._origin, since)
        if p.total_closed == 0:
            return (
                f"<b>Performa simulasi ({esc(window)})</b>\nBelum ada sinyal yang ditutup. "
                f"Terbuka: {p.open_pending} menunggu entry, {p.open_active} aktif; kedaluwarsa: {p.expired}."
            )
        return "\n".join(
            [
                f"<b>Performa simulasi ({esc(window)}, origin {esc(p.origin)})</b>",
                f"Ditutup: {p.total_closed} · menang {p.wins} · kalah {p.losses} · win rate {fmt_num(p.win_rate, 1)}%",
                f"Rata-rata R: {fmt_num(p.avg_r, 2)} · total R: {fmt_num(p.sum_r, 2)}",
                f"Terbuka: {p.open_pending} menunggu entry, {p.open_active} aktif · kedaluwarsa {p.expired}",
                "<i>Hasil simulasi sinyal (tanpa slippage), bukan transaksi aktual. Bukan jaminan hasil masa depan.</i>",
            ]
        )

    async def cmd_cek(self, cmd: IncomingCommand) -> str:
        if not cmd.args:
            return "Format: /cek KODE"
        try:
            symbol = canonical_symbol(cmd.args[0])
        except ValueError:
            return "Kode saham tidak valid."
        if self.d.aggregator is None or self.d.engine is None:
            return "Evaluasi tidak tersedia pada instance ini."
        service = self.d.service
        try:
            session = service.d.calendar.previous_trading_session(to_wib(self.d.clock()).date())
        except Exception as exc:  # noqa: BLE001
            return f"Kalender: {esc(exc)}"
        agg = await self.d.aggregator.get_ohlcv(
            symbol, Timeframe.D1, None, None, expected_last_session=session
        )
        if agg.frame is None or not agg.usable:
            return f"<b>{esc(symbol)}</b>: data tidak layak ({esc(agg.status.value)}) — {esc('; '.join(agg.issues)[:300])}"
        ev = self.d.engine.evaluate_symbol(
            symbol,
            SymbolInput(
                agg.frame, agg.status, foreign_flow_reason="tidak ada provider foreign flow aktif"
            ),
            session,
        )
        header = f"<b>/cek {esc(symbol)}</b> · sesi {esc(fmt_date(session))} · data {esc(agg.provider_used or '?')} ({esc(agg.status.value)})\n<i>Evaluasi ad hoc, BUKAN sinyal produksi.</i>"
        if ev.blocked is not None:
            return (
                f"{header}\nDilewati pada tahap {esc(ev.blocked.stage)}: {esc(ev.blocked.reason)}"
            )
        states = ", ".join(f"{esc(o.strategy_id)}={esc(o.state.value)}" for o in ev.outcomes)
        if ev.card is None:
            conf = f" · confidence {ev.confidence}" if ev.confidence is not None else ""
            return f"{header}\nTidak ada setup layak{conf}.\nStatus strategi: {states}"
        c = ev.card
        r = c.risk
        return "\n".join(
            [
                header,
                f"Setup {esc(STRATEGY_LABEL.get(c.primary_strategy, c.primary_strategy))} · confidence <b>{c.confidence}</b>",
                f"Entry {fmt_num(r.entry_low)}–{fmt_num(r.entry_high)} · SL {fmt_num(r.stop_loss)} · TP1/2/3 {fmt_num(r.tp1)}/{fmt_num(r.tp2)}/{fmt_num(r.tp3)} · R:R {fmt_num(r.rr_tp1_gross, 2)} (bersih {fmt_num(r.rr_tp1_net, 2)})",
                "Alasan: " + "; ".join(esc(x) for x in c.reasons),
                f"<i>{esc(DISCLAIMER)}</i>",
            ]
        )

    async def cmd_watchlist(self, cmd: IncomingCommand) -> str:
        sub = cmd.args[0].lower() if cmd.args else "list"
        if sub == "list":
            rows = await self.d.repo.watchlist()
            if not rows:
                return "Watchlist kosong."
            return "<b>Watchlist</b>: " + ", ".join(esc(r.symbol) for r in rows)
        if sub in ("add", "remove"):
            if len(cmd.args) < 2:
                return f"Format: /watchlist {sub} KODE"
            try:
                symbol = canonical_symbol(cmd.args[1])
            except ValueError:
                return "Kode saham tidak valid."
            if sub == "add":
                ok = await self.d.repo.add_watchlist(symbol, user_id=cmd.user_id)
                return f"✅ {esc(symbol)} ditambahkan." if ok else f"{esc(symbol)} sudah ada."
            ok = await self.d.repo.remove_watchlist(symbol, user_id=cmd.user_id)
            return f"✅ {esc(symbol)} dihapus." if ok else f"{esc(symbol)} tidak ada di watchlist."
        return "Format: /watchlist list | add KODE | remove KODE"

    # ------------------------------------------------------------------ admin
    async def cmd_pause(self, cmd: IncomingCommand) -> str:
        await self.d.repo.set_paused(True, by_user_id=cmd.user_id, reason=" ".join(cmd.args))
        return "⏸ Bot di-pause: job terjadwal dan /runnow tidak akan menerbitkan laporan sampai /resume."

    async def cmd_resume(self, cmd: IncomingCommand) -> str:
        await self.d.repo.set_paused(False, by_user_id=cmd.user_id)
        return "▶️ Bot dilanjutkan."

    async def cmd_runnow(self, cmd: IncomingCommand) -> str:
        which = cmd.args[0].lower() if cmd.args else ""
        if which not in ("morning", "afternoon"):
            return "Format: /runnow morning|afternoon"
        outcome = await self.d.service.run(ReportType(which), trigger="manual")
        return outcome_text(outcome)

    async def cmd_broadcast(self, cmd: IncomingCommand) -> str:
        text = " ".join(cmd.args).strip()
        if not text:
            return "Format: /broadcast TEKS"
        if self.d.broadcast is None:
            return "Broadcast tidak tersedia pada instance ini."
        summary = await self.d.broadcast(f"<b>📣 Pengumuman admin</b>\n{esc(text)}")  # type: ignore[misc]
        return f"Broadcast: {esc(summary)}"

    async def cmd_health(self, cmd: IncomingCommand) -> str:
        repo = self.d.repo
        paused = await repo.is_paused()
        counts = await repo.count_deliveries_by_status()
        health = await repo.latest_provider_health()
        latest = await repo.latest_completed_job(None, self._origin)
        gate = await repo.backtest_gate()
        lines = [
            "<b>Health</b>",
            f"Mode: {esc(self.d.settings.app_env)}/{esc(self.d.settings.app_mode)} · live send: {'ya' if self.d.settings.live_send_allowed else 'tidak'} · pause: {'ya' if paused else 'tidak'}",
            "Provider: "
            + (
                ", ".join(
                    f"{esc(h.provider)}={'sehat' if h.healthy else 'GAGAL'} ({esc(h.breaker_state)})"
                    for h in health
                )
                or "belum dicek"
            ),
            "Delivery: " + (", ".join(f"{esc(k)}={v}" for k, v in sorted(counts.items())) or "—"),
            f"Unknown menunggu rekonsiliasi: {counts.get('unknown', 0)}",
            f"Job terakhir selesai: {esc(latest.job_type + ' ' + latest.trading_date.isoformat()) if latest else '—'}",
            f"Gate backtest: {'lulus' if gate and gate.get('passed') else 'belum ada / belum lulus'}",
            f"Waktu server: {esc(fmt_dt(self.d.clock()))}",
        ]
        return "\n".join(lines)


def outcome_text(o: JobOutcome) -> str:
    lines = [
        f"<b>Job {esc(o.report_type.value)} {esc(o.trading_date.isoformat())}: {esc(o.status)}</b>"
    ]
    if o.reason:
        lines.append(f"Alasan: {esc(o.reason)}")
    if o.delivery is not None:
        lines.append(f"Pengiriman: {esc(o.delivery.as_text)}")
    if o.snapshot is not None:
        lines.append(
            f"Setup: {len(o.snapshot.signals)} · update sinyal: {len(o.snapshot.active_updates)} · bagian pesan: {len(o.parts)}"
        )
    if o.export_path is not None:
        lines.append(f"Ekspor: <code>{esc(o.export_path)}</code>")
    for w in o.warnings[:5]:
        lines.append(f"⚠️ {esc(w)}")
    return "\n".join(lines)
