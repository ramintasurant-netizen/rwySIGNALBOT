"""Health check container: heartbeat ada dan segar. Tidak membaca/menampilkan rahasia apa pun.

Exit 0 sehat, 1 tidak sehat. Ambang default 40 menit (health tick scheduler 15 menit + toleransi).
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

MAX_AGE_SECONDS = int(os.environ.get("HEALTHCHECK_MAX_AGE_SECONDS", "2400"))


def main() -> int:
    var_dir = Path(os.environ.get("VAR_DIR", "var"))
    heartbeat = var_dir / "heartbeat"
    if not heartbeat.exists():
        print("heartbeat belum ada")
        return 1
    try:
        stamp = datetime.fromisoformat(heartbeat.read_text(encoding="utf-8").strip())
    except ValueError:
        print("heartbeat tidak dapat dibaca")
        return 1
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - stamp).total_seconds()
    if age > MAX_AGE_SECONDS:
        print(f"heartbeat basi: {int(age)}s > {MAX_AGE_SECONDS}s")
        return 1
    print(f"sehat: heartbeat {int(age)}s lalu")
    return 0


if __name__ == "__main__":
    sys.exit(main())
