"""Provider CSV lokal untuk foreign flow dan broker summary (data yang Anda ekspor sendiri).

Yahoo tidak menyediakan data ini dan belum ada API terverifikasi, sehingga jalur yang jujur adalah
berkas CSV dari aplikasi sekuritas/terminal Anda. Skema (header wajib, tanggal WIB ``YYYY-MM-DD``):

- ``<dir>/foreign_flow/<SYMBOL>.csv``: ``date,buy_value,sell_value`` (nilai rupiah) — ``net = buy − sell``.
  Alternatif minimal: ``date,net_value``.
- ``<dir>/broker_summary/<SYMBOL>.csv``: ``date,broker,buy_value,sell_value`` (satu baris per broker per tanggal).

Baris untuk tanggal yang diminta tidak ada ⇒ ``Unavailable`` (bukan nol). Nilai nol yang tertulis
⇒ ``Ok`` dengan nol yang sah. Berkas rusak ⇒ ``Failed`` (tidak dapat diulang).
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import ClassVar

from config.common import canonical_symbol
from core.timeutil import utc_now
from data.providers.base import (
    BrokerEntry,
    BrokerSummary,
    Capability,
    DataOrigin,
    Failed,
    ForeignFlow,
    MarketDataProvider,
    OHLCVFrame,
    Ok,
    PriceBasis,
    ProviderMeta,
    ProviderResult,
    Quote,
    Timeframe,
    Unavailable,
    Unsupported,
)

MAX_CSV_BYTES = 20 * 1024 * 1024


def _dec(value: str, field: str) -> Decimal:
    try:
        return Decimal(value.strip().replace(",", ""))
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"nilai {field!r} tidak valid: {value!r}") from exc


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.stat().st_size > MAX_CSV_BYTES:
        raise ValueError(f"{path.name} melebihi {MAX_CSV_BYTES} byte")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{path.name}: tanpa header")
        reader.fieldnames = [f.strip().lower() for f in reader.fieldnames]
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


class LocalFlowProvider(MarketDataProvider):
    name: ClassVar[str] = "local_flow"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.FOREIGN_FLOW, Capability.BROKER_SUMMARY}
    )

    def __init__(self, root: Path, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._root = Path(root)
        self._clock = clock

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
        *,
        price_basis: PriceBasis = PriceBasis.RAW,
    ) -> ProviderResult[OHLCVFrame]:
        return Unsupported(self.name, "provider CSV lokal hanya untuk foreign flow/broker summary")

    async def get_quote(self, symbol: str) -> ProviderResult[Quote]:
        return Unsupported(self.name, "provider CSV lokal tidak menyediakan quote")

    async def get_foreign_flow(
        self, symbol: str, session_date: date
    ) -> ProviderResult[ForeignFlow]:
        sym = canonical_symbol(symbol)
        path = self._root / "foreign_flow" / f"{sym}.csv"
        try:
            rows = _read_rows(path)
        except FileNotFoundError:
            return Unavailable(
                self.name, f"tidak ada berkas foreign flow untuk {sym} ({path.name})"
            )
        except (ValueError, OSError, csv.Error) as exc:
            return Failed(self.name, f"{path.name}: {exc}")
        fetched = self._clock()
        for row in rows:
            if row.get("date") != session_date.isoformat():
                continue
            try:
                if "net_value" in row and row["net_value"] != "":
                    net = _dec(row["net_value"], "net_value")
                    buy = _dec(row["buy_value"], "buy_value") if row.get("buy_value") else None
                    sell = _dec(row["sell_value"], "sell_value") if row.get("sell_value") else None
                else:
                    buy = _dec(row["buy_value"], "buy_value")
                    sell = _dec(row["sell_value"], "sell_value")
                    net = buy - sell
            except (ValueError, KeyError) as exc:
                return Failed(self.name, f"{path.name} {session_date}: {exc}")
            flow = ForeignFlow(
                sym,
                session_date,
                net,
                self.name,
                fetched,
                buy_value=buy,
                sell_value=sell,
                origin=DataOrigin.LIVE,
            )
            return Ok(
                flow,
                ProviderMeta(
                    self.name, fetched, None, DataOrigin.LIVE, ("sumber: CSV lokal pengguna",)
                ),
            )
        return Unavailable(self.name, f"tidak ada baris foreign flow {sym} untuk {session_date}")

    async def get_broker_summary(
        self, symbol: str, session_date: date
    ) -> ProviderResult[BrokerSummary]:
        sym = canonical_symbol(symbol)
        path = self._root / "broker_summary" / f"{sym}.csv"
        try:
            rows = _read_rows(path)
        except FileNotFoundError:
            return Unavailable(
                self.name, f"tidak ada berkas broker summary untuk {sym} ({path.name})"
            )
        except (ValueError, OSError, csv.Error) as exc:
            return Failed(self.name, f"{path.name}: {exc}")
        entries: list[BrokerEntry] = []
        seen: set[str] = set()
        for row in rows:
            if row.get("date") != session_date.isoformat():
                continue
            try:
                code = row["broker"].strip().upper()
                if not code:
                    raise ValueError("kode broker kosong")
                if code in seen:
                    raise ValueError(f"broker {code} duplikat pada {session_date}")
                seen.add(code)
                entries.append(
                    BrokerEntry(
                        code,
                        _dec(row["buy_value"], "buy_value"),
                        _dec(row["sell_value"], "sell_value"),
                    )
                )
            except (ValueError, KeyError) as exc:
                return Failed(self.name, f"{path.name} {session_date}: {exc}")
        if not entries:
            return Unavailable(
                self.name, f"tidak ada baris broker summary {sym} untuk {session_date}"
            )
        fetched = self._clock()
        summary = BrokerSummary(
            sym, session_date, tuple(entries), self.name, fetched, origin=DataOrigin.LIVE
        )
        return Ok(
            summary,
            ProviderMeta(
                self.name, fetched, None, DataOrigin.LIVE, ("sumber: CSV lokal pengguna",)
            ),
        )

    async def health_check(self) -> bool:
        return (self._root / "foreign_flow").is_dir() or (self._root / "broker_summary").is_dir()
