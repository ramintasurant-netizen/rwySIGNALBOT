"""Konteks makro global (indeks AS, EIDO, USD/IDR, komoditas) dari konfigurasi instrumen.

Instrumen tanpa simbol/sumber terverifikasi dilaporkan ``unavailable`` dengan alasan;
tidak pernah diganti proxy secara diam-diam.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.common import ConfigError, VerificationMeta, load_yaml
from core.redaction import redact_exception
from core.timeutil import to_utc, utc_now
from data.providers.yahoo import Downloader, yfinance_downloader


class MacroInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=80)
    symbol: str | None = None
    source: Literal["yahoo"] | None = None
    unit: str = ""
    verified: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> MacroInstrument:
        if (self.symbol is None) != (self.source is None):
            raise ValueError(
                f"instrumen {self.id}: symbol dan source harus diisi bersama atau kosong bersama"
            )
        return self

    @property
    def configured(self) -> bool:
        return self.symbol is not None and self.source is not None


class GlobalMacroConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    meta: VerificationMeta
    instruments: list[MacroInstrument] = []

    @model_validator(mode="after")
    def _unique_ids(self) -> GlobalMacroConfig:
        ids = [i.id for i in self.instruments]
        if len(ids) != len(set(ids)):
            raise ValueError("id instrumen makro duplikat")
        return self


def load_global_macro_config(path: Path) -> GlobalMacroConfig:
    try:
        return GlobalMacroConfig.model_validate(load_yaml(path))
    except ValueError as exc:
        raise ConfigError(f"global_macro tidak valid ({path}): {exc}") from exc


@dataclass(frozen=True, slots=True)
class MacroItem:
    id: str
    label: str
    unit: str
    status: Literal["ok", "unavailable", "failed"]
    verified: bool
    last: Decimal | None = None
    previous: Decimal | None = None
    change_pct: Decimal | None = None
    as_of: datetime | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class GlobalContext:
    fetched_at: datetime
    items: tuple[MacroItem, ...]

    @property
    def available(self) -> tuple[MacroItem, ...]:
        return tuple(i for i in self.items if i.status == "ok")

    @property
    def all_unavailable(self) -> bool:
        return not self.available


def _change_pct(last: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return ((last - previous) / previous * 100).quantize(Decimal("0.01"))


class GlobalMacroProvider:
    def __init__(
        self,
        config: GlobalMacroConfig,
        *,
        downloader: Downloader | None = None,
        max_concurrency: int = 4,
        timeout_seconds: float = 20.0,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._config = config
        self._download = downloader or yfinance_downloader
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._timeout = timeout_seconds
        self._clock = clock

    async def _fetch_daily(self, symbol: str) -> pd.DataFrame:
        async with self._semaphore:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._download, symbol, interval="1d", start=None, end=None, period="10d"
                ),
                timeout=self._timeout,
            )

    async def _item(self, instrument: MacroInstrument) -> MacroItem:
        base = {
            "id": instrument.id,
            "label": instrument.label,
            "unit": instrument.unit,
            "verified": instrument.verified,
        }
        if not instrument.configured:
            return MacroItem(
                status="unavailable",
                reason=instrument.notes or "sumber belum dikonfigurasi/terverifikasi",
                **base,
            )
        try:
            raw = await self._fetch_daily(instrument.symbol or "")
        except TimeoutError:
            return MacroItem(status="failed", reason=f"timeout {self._timeout}s", **base)
        except Exception as exc:  # noqa: BLE001
            return MacroItem(status="failed", reason=redact_exception(exc), **base)
        if raw is None or raw.empty:
            return MacroItem(status="unavailable", reason="provider mengembalikan 0 bar", **base)

        cols = {str(c).strip().lower(): c for c in raw.columns}
        if "close" not in cols:
            return MacroItem(status="failed", reason="kolom close tidak ada", **base)
        closes = pd.to_numeric(raw[cols["close"]], errors="coerce").dropna()
        if closes.empty:
            return MacroItem(status="unavailable", reason="close kosong", **base)
        index = pd.DatetimeIndex(closes.index)
        if index.tz is None:
            index = index.tz_localize("UTC")
        as_of = index[-1].tz_convert("UTC").to_pydatetime()
        last = Decimal(repr(float(closes.iloc[-1])))
        previous = Decimal(repr(float(closes.iloc[-2]))) if len(closes) >= 2 else None
        return MacroItem(
            status="ok",
            last=last,
            previous=previous,
            change_pct=_change_pct(last, previous) if previous is not None else None,
            as_of=as_of,
            **base,
        )

    async def snapshot(self) -> GlobalContext:
        fetched_at = to_utc(self._clock())
        items = await asyncio.gather(*(self._item(i) for i in self._config.instruments))
        return GlobalContext(fetched_at=fetched_at, items=tuple(items))
