"""Template adapter Sectors — NONAKTIF sampai kontrak API diverifikasi."""

from __future__ import annotations

from typing import ClassVar

from data.providers.base import Capability
from data.providers.templates import UnverifiedProviderTemplate


class SectorsProvider(UnverifiedProviderTemplate):
    name: ClassVar[str] = "sectors"
    display_name: ClassVar[str] = "Sectors"
    planned_capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.OHLCV_DAILY, Capability.FOREIGN_FLOW}
    )
    verification_items: ClassVar[tuple[str, ...]] = (
        "Endpoint histori harga harian dan data transaksi asing (net foreign buy/sell)",
        "Definisi 'foreign flow' yang dipakai (nilai vs volume, pasar reguler vs seluruh pasar)",
        "Zona waktu dan keterlambatan publikasi data harian",
        "Skema autentikasi, batas rate, biaya",
        "Lisensi penggunaan/redistribusi data",
    )
