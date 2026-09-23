"""Rekonsiliasi manual pengiriman berstatus ``unknown`` (respons Telegram hilang).

    python scripts/reconcile_deliveries.py list
    python scripts/reconcile_deliveries.py mark-sent ID --by "nama"     # sudah dicek: pesan ADA di grup
    python scripts/reconcile_deliveries.py mark-failed ID --by "nama"   # sudah dicek: pesan TIDAK ada
    python scripts/reconcile_deliveries.py requeue ID                   # failed → pending (dikirim ulang oleh job berikutnya/manual)

Tidak ada pengiriman ulang otomatis di sini; skrip hanya mengubah status setelah pengecekan manusia.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import load_settings  # noqa: E402
from storage.repository import Database, Repository  # noqa: E402


async def run(args: argparse.Namespace) -> int:
    settings = load_settings(None if args.env_file == "-" else args.env_file)
    db = Database(settings.database_url)
    if db.is_sqlite:
        await db.init_dev_schema()
    repo = Repository(db)
    try:
        if args.action == "list":
            rows = await repo.unknown_deliveries()
            if not rows:
                print("Tidak ada pengiriman berstatus unknown.")
                return 0
            for r in rows:
                print(
                    f"id={r.id} job={r.job_run_id} chat={r.chat_id} thread={r.thread_id} bagian={r.part_index + 1}/{r.part_count} attempts={r.attempts} updated={r.updated_at.isoformat()} error={r.error}"
                )
            return 0
        if args.action in ("mark-sent", "mark-failed"):
            status = "sent" if args.action == "mark-sent" else "failed"
            ok = await repo.resolve_unknown(args.id, status, resolved_by=args.by)
            print(
                f"id={args.id} → {status}"
                if ok
                else f"id={args.id} tidak berstatus unknown / tidak ada"
            )
            return 0 if ok else 1
        if args.action == "requeue":
            ok = await repo.requeue_failed(args.id)
            print(
                f"id={args.id} → pending"
                if ok
                else f"id={args.id} tidak berstatus failed / tidak ada"
            )
            return 0 if ok else 1
        return 2
    finally:
        await db.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--env-file", default=".env")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("list")
    for name in ("mark-sent", "mark-failed"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument(
            "--by", required=True, help="identitas yang melakukan pengecekan, mis. admin:42"
        )
    p = sub.add_parser("requeue")
    p.add_argument("id", type=int)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
