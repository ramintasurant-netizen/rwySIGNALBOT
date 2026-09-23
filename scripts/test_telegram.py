"""Uji koneksi/kirim Telegram TANPA engine.

    python scripts/test_telegram.py            # dry-run: getMe + verifikasi tujuan, tanpa kirim
    python scripts/test_telegram.py --send     # kirim SATU pesan TEST (persetujuan eksplisit)
    python scripts/test_telegram.py --send --chat-id -1001234567890   # wajib bila tujuan > 1
    python scripts/test_telegram.py --discover  # tampilkan ID grup yang baru mengirim command ke bot

Token & tujuan dibaca dari environment/.env. Hanya tujuan dalam allowlist; chat private ditolak.
Sukses dilaporkan hanya bila API Telegram mengonfirmasi (message_id). Timeout = AMBIGU, tidak diulang.
Skrip ini tidak mengambil alih polling/webhook bot.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.formatter import esc, format_test_message  # noqa: E402
from config.settings import ChatTarget, Settings, load_settings  # noqa: E402
from core.logging import configure_logging  # noqa: E402
from core.redaction import redact_exception  # noqa: E402
from notifications.base import DeliveryStatus, TargetRejectedError  # noqa: E402
from notifications.telegram import BotClient, TelegramNotifier  # noqa: E402

EXIT_OK = 0
EXIT_REJECTED = 2  # validasi lokal/API menolak; tidak ada pesan terkirim
EXIT_FAILED = 3  # gagal sebelum/tanpa terkirim
EXIT_AMBIGUOUS = 4  # request terkirim, respons hilang


def _select_target(settings: Settings, chat_id: int | None) -> tuple[ChatTarget | None, str]:
    targets = list(settings.signal_targets)
    if settings.admin_target is not None:
        targets.append(settings.admin_target)
    if not targets:
        return None, "tidak ada tujuan pada TELEGRAM_SIGNAL_CHAT_IDS/TELEGRAM_ADMIN_CHAT_ID"
    if chat_id is None:
        if len(targets) > 1:
            return (
                None,
                f"tujuan lebih dari satu ({', '.join(str(t) for t in targets)}); berikan --chat-id eksplisit",
            )
        return targets[0], ""
    for t in targets:
        if t.chat_id == chat_id:
            return t, ""
    return None, f"--chat-id {chat_id} tidak ada dalam allowlist"


async def discover(settings: Settings, bot: BotClient | None = None) -> int:
    """Cetak chat grup/channel yang terlihat lewat getUpdates (bot harus TIDAK sedang polling).

    Ketik /start di grup setelah bot ditambahkan, lalu jalankan perintah ini. Chat private
    tidak ditampilkan dan tidak disimpan. Token tetap di mesin Anda.
    """
    if settings.telegram_bot_token is None:
        print("DITOLAK: TELEGRAM_BOT_TOKEN kosong.")
        return EXIT_REJECTED
    if bot is None:
        from telegram import Bot

        bot = Bot(settings.telegram_bot_token.get_secret_value())
    try:
        updates = await bot.get_updates(timeout=0)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        print(
            f"GAGAL: getUpdates — {redact_exception(exc)} "
            "(pastikan bot tidak sedang berjalan/polling)"
        )
        return EXIT_FAILED
    seen: dict[int, tuple[str, str]] = {}
    for u in updates:
        obj = getattr(u, "effective_chat", None)
        if obj is None or str(obj.type) == "private":
            continue
        seen[int(obj.id)] = (str(obj.type), str(getattr(obj, "title", "") or ""))
    if not seen:
        print(
            "Belum ada grup/channel terlihat. Tambahkan bot ke grup, ketik /start di grup, lalu ulangi."
        )
        return EXIT_OK
    print(
        "Chat grup/channel yang terlihat (salin ID negatifnya ke "
        "TELEGRAM_SIGNAL_CHAT_IDS / TELEGRAM_ADMIN_CHAT_ID):"
    )
    for cid, (ctype, title) in seen.items():
        print(f"  {cid}  ({ctype})  {title}")
    return EXIT_OK


async def run(
    settings: Settings, *, send: bool, chat_id: int | None, bot: BotClient | None = None
) -> int:
    if settings.telegram_bot_token is None:
        print("DITOLAK: TELEGRAM_BOT_TOKEN kosong.")
        return EXIT_REJECTED
    target, reason = _select_target(settings, chat_id)
    if target is None:
        print(f"DITOLAK: {reason}")
        return EXIT_REJECTED
    if bot is None:
        from telegram import Bot

        bot = Bot(settings.telegram_bot_token.get_secret_value())
    notifier = TelegramNotifier(bot, allowed_targets=(target,))
    try:
        me = await bot.get_me()
        print(f"Bot: @{getattr(me, 'username', '?')} (id {getattr(me, 'id', '?')})")
    except Exception as exc:  # noqa: BLE001
        print(f"GAGAL: getMe — {redact_exception(exc)}")
        return EXIT_FAILED
    ver = await notifier.verify_target(target, force=True)
    print(
        f"Tujuan {target}: tipe={ver.chat_type} judul={ver.title!r} status_bot={ver.bot_status} → {'OK' if ver.ok else 'DITOLAK: ' + ver.reason}"
    )
    if not ver.ok:
        return EXIT_REJECTED
    if not send:
        print(
            "Dry-run selesai: tidak ada pesan dikirim. Tambahkan --send untuk mengirim satu pesan TEST."
        )
        return EXIT_OK
    text = esc(format_test_message()).replace("\n", "\n")
    try:
        result = await notifier.send(target, text)
    except TargetRejectedError as exc:
        print(f"DITOLAK: {exc}")
        return EXIT_REJECTED
    if result.status is DeliveryStatus.SENT:
        print(
            f"SUKSES: API Telegram mengonfirmasi message_id={result.message_id} ke {target}. (Bukan bukti anggota sudah membaca.)"
        )
        return EXIT_OK
    if result.status is DeliveryStatus.FAILED:
        print(f"GAGAL sebelum terkirim: {result.error}")
        return EXIT_FAILED
    print(
        f"AMBIGU: request mungkin terkirim tetapi respons hilang ({result.error}). TIDAK dikirim ulang otomatis; cek grup secara manual."
    )
    return EXIT_AMBIGUOUS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--send", action="store_true", help="persetujuan eksplisit mengirim satu pesan TEST"
    )
    parser.add_argument(
        "--chat-id", type=int, default=None, help="tujuan eksplisit (wajib bila allowlist > 1)"
    )
    parser.add_argument(
        "--discover",
        action="store_true",
        help="tampilkan ID grup/channel yang terlihat bot (tanpa kirim)",
    )
    parser.add_argument("--env-file", default=".env")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(None if args.env_file == "-" else args.env_file)
    except Exception as exc:  # noqa: BLE001
        print(f"Konfigurasi tidak valid:\n{exc}", file=sys.stderr)
        return EXIT_REJECTED
    configure_logging(level="WARNING")
    if args.discover:
        return asyncio.run(discover(settings))
    return asyncio.run(run(settings, send=args.send, chat_id=args.chat_id))


if __name__ == "__main__":
    raise SystemExit(main())
