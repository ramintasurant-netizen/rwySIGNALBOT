"""Migrasi Alembic harus menghasilkan skema yang sama dengan model (diuji pada SQLite)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]


def test_alembic_upgrade_head_matches_models(tmp_path) -> None:
    db_path = tmp_path / "mig.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}", "PYTHONPATH": str(ROOT)}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    engine = sa.create_engine(f"sqlite:///{db_path}")
    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "job_runs",
        "signals",
        "signal_updates",
        "subscribers",
        "watchlist",
        "message_deliveries",
        "provider_health",
        "app_state",
        "alembic_version",
    }
    assert expected <= tables
    cols = {c["name"] for c in inspector.get_columns("message_deliveries")}
    assert {
        "job_run_id",
        "chat_id",
        "thread_id",
        "part_index",
        "status",
        "message_id",
        "content_sha256",
    } <= cols
    uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("job_runs")}
    assert ("job_type", "trading_date", "origin") in uniques
    engine.dispose()
