"""Sumber data backtest: aggregator (Yahoo, development) atau CSV lokal per simbol.

CSV: kolom ``date,open,high,low,close,volume`` (tanggal WIB, ``YYYY-MM-DD``); dinormalisasi lewat
``normalize_ohlcv`` yang sama dengan data live dan diberi origin ``fixture`` agar tidak tercampur.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from config.common import canonical_symbol
from data.aggregator import MarketDataAggregator
from data.providers.base import DataOrigin, OHLCVFrame, Timeframe, normalize_ohlcv


def load_csv_frame(
    path: Path, *, symbol: str | None = None, raw_symbol: str | None = None
) -> OHLCVFrame:
    """``raw_symbol`` dipakai apa adanya (mis. indeks ``^JKSE``); selain itu kode saham dikanonikkan."""
    sym = raw_symbol if raw_symbol else canonical_symbol(symbol or path.stem)
    raw = pd.read_csv(path)
    cols = {c.lower(): c for c in raw.columns}
    for required in ("date", "open", "high", "low", "close", "volume"):
        if required not in cols:
            raise ValueError(f"{path}: kolom {required} tidak ada")
    raw.index = pd.to_datetime(raw[cols["date"]])
    raw = raw.drop(columns=[cols["date"]])
    now = datetime.now(UTC)
    return normalize_ohlcv(
        raw,
        symbol=sym,
        timeframe=Timeframe.D1,
        provider=f"csv:{path.name}",
        fetched_at=now,
        now=now,
        origin=DataOrigin.FIXTURE,
        notes=("data CSV lokal: asal & lisensi tanggung jawab pengguna",),
    )


def load_csv_dir(folder: Path) -> dict[str, OHLCVFrame]:
    frames = {f.symbol: f for f in (load_csv_frame(p) for p in sorted(folder.glob("*.csv")))}
    if not frames:
        raise ValueError(f"tidak ada berkas CSV di {folder}")
    return frames


async def fetch_frames(
    aggregator: MarketDataAggregator, symbols: list[str], *, start: date, end: date, min_bars: int
) -> tuple[dict[str, OHLCVFrame], dict[str, str]]:
    """Ambil OHLCV harian lewat aggregator; simbol yang tidak layak dilaporkan terpisah."""
    # Mulai lebih awal agar warmup terpenuhi (≈ 1.6 hari kalender per hari perdagangan + buffer).
    fetch_start = datetime.combine(
        start - timedelta(days=int(min_bars * 1.6) + 30), datetime.min.time(), tzinfo=UTC
    )
    fetch_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    sem = asyncio.Semaphore(4)

    async def one(sym: str):
        async with sem:
            return sym, await aggregator.get_ohlcv(
                sym, Timeframe.D1, fetch_start, fetch_end, min_bars=min_bars
            )

    results = await asyncio.gather(*(one(s) for s in symbols))
    frames: dict[str, OHLCVFrame] = {}
    skipped: dict[str, str] = {}
    for sym, agg in results:
        if agg.frame is not None and agg.usable:
            frames[sym] = agg.frame
        else:
            skipped[sym] = f"{agg.status.value}: " + "; ".join(agg.issues)[:200]
    return frames, skipped
