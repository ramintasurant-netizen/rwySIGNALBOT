"""CLI backtest dengan CSV sintetis lokal (tanpa jaringan)."""

from __future__ import annotations

import json
from pathlib import Path

import main
from backtest.data import load_csv_frame
from tests.engine_fixtures import breakout_frame, flat_frame


def _write_csv(folder: Path, symbol: str, frame) -> None:
    df = frame.frame.copy()
    df["date"] = [d.isoformat() for d in df["session_date"]]
    df[["date", "open", "high", "low", "close", "volume"]].to_csv(
        folder / f"{symbol}.csv", index=False
    )


def test_csv_loader_normalizes_and_labels_fixture(tmp_path) -> None:
    _write_csv(tmp_path, "BRKO", breakout_frame("BRKO"))
    frame = load_csv_frame(tmp_path / "BRKO.csv")
    assert (
        frame.symbol == "BRKO"
        and frame.origin.value == "fixture"
        and frame.provider.startswith("csv:")
    )
    assert str(frame.frame.index.tz) == "UTC" and frame.frame["complete"].all()
    assert len(frame) == 300


def test_backtest_cli_with_csv_writes_report_and_gate_fails_honestly(
    tmp_path, monkeypatch, capsys
) -> None:
    _write_csv(tmp_path, "BRKO", breakout_frame("BRKO"))
    _write_csv(tmp_path, "FLAT", flat_frame("FLAT"))
    var = tmp_path / "var"
    monkeypatch.setenv("VAR_DIR", str(var))
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{var / 'bt.db'}")
    sessions = list(breakout_frame("BRKO").frame["session_date"])
    start, end = sessions[255], sessions[-1]
    code = main.main(
        [
            "--env-file",
            "-",
            "backtest",
            "--start",
            start.isoformat(),
            "--end",
            end.isoformat(),
            "--csv-dir",
            str(tmp_path),
            "--save-gate",
        ]
    )
    out = capsys.readouterr().out
    report = json.loads(out[: out.rfind("}") + 1])
    assert code == 4  # gate tidak lulus (trade OOS < minimum) → kode 4, bukan dipaksa lulus
    assert report["gate"]["passed"] is False
    assert any(not c["ok"] for c in report["gate"]["checks"].values())
    assert report["out_of_sample"]["metrics"]["trades"] >= 0
    assert "survivorship" in " ".join(report["out_of_sample"]["limitations"])
    assert report["rules_label"].startswith("CONTOH")
    out_dir = Path(report["output_dir"])
    assert (out_dir / "report.json").exists() and (out_dir / "oos_equity.csv").exists()
    # gate TIDAK lulus tetap tersimpan sebagai passed=false → gate produksi tetap memblokir
    import asyncio

    from storage.repository import Database, Repository

    async def read():
        db = Database(f"sqlite+aiosqlite:///{var / 'bt.db'}")
        try:
            return await Repository(db).backtest_gate()
        finally:
            await db.dispose()

    stored = asyncio.run(read())
    assert stored is not None and stored["passed"] is False and stored["config_hash"]
