"""Watchlist global (dikelola admin). Bukan rekomendasi; hanya universe awal screener."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from config.common import ConfigError, VerificationMeta, canonical_symbol, load_yaml


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str = ""
    notes: str = ""

    @field_validator("symbol")
    @classmethod
    def _canonical(cls, value: str) -> str:
        return canonical_symbol(value)


class Watchlist(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    meta: VerificationMeta
    symbols: list[WatchlistEntry] = []

    @model_validator(mode="after")
    def _unique(self) -> Watchlist:
        seen: set[str] = set()
        for entry in self.symbols:
            if entry.symbol in seen:
                raise ValueError(f"simbol duplikat pada watchlist: {entry.symbol}")
            seen.add(entry.symbol)
        return self

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(entry.symbol for entry in self.symbols)


def load_watchlist(path: Path) -> Watchlist:
    try:
        return Watchlist.model_validate(load_yaml(path))
    except ValueError as exc:
        raise ConfigError(f"watchlist tidak valid ({path}): {exc}") from exc
