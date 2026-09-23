"""Tipe bersama untuk berkas konfigurasi YAML."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

UNVERIFIED_LABEL = "CONTOH / BELUM TERVERIFIKASI"
MAX_CONFIG_BYTES = 2 * 1024 * 1024

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,8}(-[A-Z0-9]{1,4})?$")


class ConfigError(ValueError):
    """Berkas konfigurasi tidak valid atau tidak dapat dibaca."""


class VerificationMeta(BaseModel):
    """Metadata verifikasi yang wajib menyertai setiap blok aturan."""

    model_config = ConfigDict(extra="forbid")

    label: str = UNVERIFIED_LABEL
    source: str = ""
    effective_from: date | None = None
    scope: list[str] = []
    verified: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _verified_requires_provenance(self) -> VerificationMeta:
        if self.verified:
            if not self.source.strip():
                raise ValueError("meta.verified=true membutuhkan meta.source (dokumen resmi)")
            if self.effective_from is None:
                raise ValueError("meta.verified=true membutuhkan meta.effective_from")
            if self.label == UNVERIFIED_LABEL:
                raise ValueError(
                    "meta.verified=true tidak boleh memakai label CONTOH / BELUM TERVERIFIKASI"
                )
        return self

    @property
    def display_label(self) -> str:
        return self.label if self.verified else UNVERIFIED_LABEL


def canonical_symbol(raw: str) -> str:
    """Normalisasi kode saham IDX: huruf besar, tanpa suffix provider (mis. ``.JK``)."""
    symbol = raw.strip().upper()
    if symbol.endswith(".JK"):
        symbol = symbol[:-3]
    if not _SYMBOL_RE.match(symbol):
        raise ValueError(f"kode saham tidak valid: {raw!r}")
    return symbol


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"berkas konfigurasi tidak ditemukan: {path}")
    if path.stat().st_size > MAX_CONFIG_BYTES:
        raise ConfigError(f"berkas konfigurasi terlalu besar (> {MAX_CONFIG_BYTES} byte): {path}")
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"isi {path} harus berupa mapping YAML")
    return loaded
