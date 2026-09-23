"""Buat arsip ZIP distribusi proyek berbasis ALLOWLIST (kode, konfigurasi contoh, dokumentasi).

    python scripts/package_project.py                 # -> dist/stock_signal_bot_<tanggal>_<commit>.zip
    python scripts/package_project.py --output x.zip --check

Tidak menyertakan: .env*, .git, var/ (database/log/cache/ekspor), virtualenv, __pycache__, .pytest_cache,
metadata platform (.hoplite/.context/.idea/.vscode), arsip ZIP lain. .env.example DISERTAKAN.
Setiap berkas dipindai pola token/kunci; bila ada kecocokan di luar berkas test, arsip dibatalkan.
Isi rahasia tidak pernah dicetak — hanya nama berkas dan pola.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOW_FILES = (
    "main.py",
    "pyproject.toml",
    "uv.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "alembic.ini",
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".gitignore",
    ".env.example",
    "README.md",
    "ARCHITECTURE.md",
)
ALLOW_DIRS = (
    "config",
    "core",
    "data",
    "engine",
    "ai",
    "bot",
    "notifications",
    "storage",
    "backtest",
    "scripts",
    "docs",
    "tests",
    "ci",
)
ALLOW_SUFFIXES = {
    ".py",
    ".yaml",
    ".yml",
    ".md",
    ".ini",
    ".mako",
    ".toml",
    ".txt",
    ".lock",
    ".example",
}
DENY_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "var",
    ".git",
    ".hoplite",
    ".context",
}
SECRET_PATTERNS = (
    re.compile(r"(?<!\d)\d{6,}:[A-Za-z0-9_-]{30,}"),  # token bot Telegram
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),  # kunci gaya OpenAI
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),  # kunci Anthropic
    re.compile(
        r"(?i)^(TELEGRAM_BOT_TOKEN|LLM_API_KEY|GOAPI_API_KEY|SECTORS_API_KEY|BROKER_X_API_KEY|POSTGRES_PASSWORD)=\S+",
        re.M,
    ),
)
FAKE_TOKEN_MARKER = "AAFakeTokenValueForTests"  # noqa: S105 - penanda token palsu test, bukan rahasia


_PLACEHOLDERS = {"", "...", "<isi>", "<token>", "<api-key>", "<password>", "xxx", "changeme"}


def _is_placeholder_assignment(token: str) -> bool:
    """Baris `KEY=nilai` dengan nilai kosong/placeholder eksplisit bukan rahasia."""
    if "=" not in token:
        return False
    value = token.split("=", 1)[1].strip().strip("\"'")
    return value.lower() in _PLACEHOLDERS or (value.startswith("<") and value.endswith(">"))


def _git_short_sha() -> str:
    try:
        git = shutil.which("git")
        if git is None:
            return "nogit"
        out = subprocess.run(  # noqa: S603 - argumen tetap, bukan input pengguna
            [git, "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() or "nogit"
    except (OSError, subprocess.SubprocessError):
        return "nogit"


def collect_files() -> list[Path]:
    files: list[Path] = []
    for name in ALLOW_FILES:
        path = ROOT / name
        if path.is_file():
            files.append(path)
    for folder in ALLOW_DIRS:
        base = ROOT / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in DENY_PARTS for part in path.relative_to(ROOT).parts):
                continue
            if path.suffix.lower() not in ALLOW_SUFFIXES and path.name != ".gitkeep":
                continue
            if path.name.startswith(".env") and path.name != ".env.example":
                continue
            files.append(path)
    return files


def scan_secrets(files: list[Path]) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in SECRET_PATTERNS:
            for m in pattern.finditer(text):
                token = m.group(0)
                if FAKE_TOKEN_MARKER in token or rel.startswith("tests/"):
                    continue
                if _is_placeholder_assignment(token):
                    continue
                findings.append((rel, pattern.pattern[:40]))
                break
    return findings


def build_zip(output: Path, files: list[Path]) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    prefix = "stock_signal_bot/"
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, prefix + path.relative_to(ROOT).as_posix())
        zf.writestr(prefix + "var/.gitkeep", "")
        zf.writestr(
            prefix + "PACKAGE_INFO.txt",
            "\n".join(
                [
                    "stock_signal_bot — arsip distribusi",
                    f"dibuat: {datetime.now(UTC).isoformat()}",
                    f"commit: {_git_short_sha()}",
                    f"berkas: {len(files)}",
                    "Tidak memuat .env, database, log, cache, atau metadata internal.",
                    "Mulai: salin .env.example ke .env, lalu ikuti README.md.",
                    "",
                ]
            ),
        )
    return len(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--output", help="path ZIP keluaran (default dist/stock_signal_bot_<tgl>_<commit>.zip)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="hanya daftar berkas + pindai rahasia, tanpa menulis ZIP",
    )
    args = parser.parse_args(argv)
    files = collect_files()
    findings = scan_secrets(files)
    if findings:
        print(
            "DIBATALKAN: pola rahasia terdeteksi pada berkas berikut (isi tidak dicetak):",
            file=sys.stderr,
        )
        for rel, pattern in findings:
            print(f"  {rel}  [{pattern}]", file=sys.stderr)
        return 2
    if args.check:
        for path in files:
            print(path.relative_to(ROOT).as_posix())
        print(f"{len(files)} berkas lolos pemeriksaan.")
        return 0
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    output = (
        Path(args.output)
        if args.output
        else ROOT / "dist" / f"stock_signal_bot_{stamp}_{_git_short_sha()}.zip"
    )
    n = build_zip(output, files)
    print(f"ZIP dibuat: {output} ({n} berkas, {output.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
