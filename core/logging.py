"""Konfigurasi loguru: log terstruktur, rotasi, redaksi rahasia pada pesan dan traceback."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path
from typing import Any

from loguru import logger

from core.redaction import redact


def _redacting_patcher(record: dict[str, Any]) -> None:
    record["message"] = redact(record["message"])
    exc = record.get("exception")
    if exc is not None and exc.value is not None:
        formatted = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
        redacted = redact(formatted)
        if redacted != formatted:
            # Traceback memuat rahasia: ganti dengan versi teredaksi sebagai teks biasa.
            record["message"] += "\n" + redacted
            record["exception"] = None


def configure_logging(
    *,
    level: str = "INFO",
    log_dir: Path | None = None,
    diagnose: bool = False,
    serialize: bool = False,
) -> None:
    logger.remove()
    logger.configure(patcher=_redacting_patcher)
    logger.add(
        sys.stderr,
        level=level,
        backtrace=False,
        diagnose=diagnose,
        serialize=serialize,
    )
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_dir / "bot.log",
            level=level,
            rotation="10 MB",
            retention="14 days",
            compression="zip",
            backtrace=False,
            diagnose=diagnose,
            serialize=True,
            enqueue=True,
        )
