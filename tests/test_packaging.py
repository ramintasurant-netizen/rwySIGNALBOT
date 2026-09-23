from __future__ import annotations

import zipfile
from pathlib import Path

from scripts import package_project

ROOT = Path(__file__).resolve().parents[1]


def test_allowlist_excludes_secrets_runtime_and_metadata(tmp_path, monkeypatch) -> None:
    files = package_project.collect_files()
    rels = {p.relative_to(ROOT).as_posix() for p in files}
    assert "main.py" in rels and ".env.example" in rels and "config/market_rules.yaml" in rels
    assert "Dockerfile" in rels and "docker-compose.yml" in rels and "uv.lock" in rels
    assert not any(r == ".env" or r.startswith("var/") or r.startswith(".git/") for r in rels)
    assert not any("__pycache__" in r or ".venv" in r or ".hoplite" in r for r in rels)
    assert not any(r.endswith((".db", ".log", ".zip")) for r in rels)


def test_build_zip_and_contents(tmp_path) -> None:
    files = package_project.collect_files()
    out = tmp_path / "pkg.zip"
    n = package_project.build_zip(out, files)
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert all(n.startswith("stock_signal_bot/") for n in names)
        assert (
            "stock_signal_bot/PACKAGE_INFO.txt" in names
            and "stock_signal_bot/var/.gitkeep" in names
        )
        assert "stock_signal_bot/.env.example" in names and "stock_signal_bot/.env" not in names
        assert len(names) == n + 2
        info = zf.read("stock_signal_bot/PACKAGE_INFO.txt").decode()
        assert "Tidak memuat .env" in info


def test_secret_scan_blocks_real_looking_token(tmp_path, monkeypatch) -> None:
    fake_repo = tmp_path / "repo"
    (fake_repo / "config").mkdir(parents=True)
    (fake_repo / "main.py").write_text("print('ok')\n")
    (fake_repo / "config" / "bad.yaml").write_text(
        "token: 987654321:XyZRealLookingTokenValue_abcdefghijklmnopq\n"
    )
    monkeypatch.setattr(package_project, "ROOT", fake_repo)
    files = package_project.collect_files()
    findings = package_project.scan_secrets(files)
    assert findings and findings[0][0] == "config/bad.yaml"
    assert package_project.main(["--check"]) == 2


def test_cli_check_on_real_repo_passes(capsys) -> None:
    assert package_project.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "berkas lolos pemeriksaan" in out and "main.py" in out
