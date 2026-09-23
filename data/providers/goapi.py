"""Template adapter GoAPI — NONAKTIF sampai kontrak API diverifikasi."""

from __future__ import annotations

from typing import ClassVar

from data.providers.base import Capability
from data.providers.templates import UnverifiedProviderTemplate


class GoAPIProvider(UnverifiedProviderTemplate):
    name: ClassVar[str] = "goapi"
    display_name: ClassVar[str] = "GoAPI"
    planned_capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.OHLCV_DAILY, Capability.QUOTE}
    )
    verification_items: ClassVar[tuple[str, ...]] = (
        "URL dasar dan daftar endpoint resmi (histori harian, quote, indeks)",
        "Skema autentikasi (header/query) dan cara rotasi kunci",
        "Skema field response: nama kolom OHLCV, zona waktu timestamp, satuan volume/nilai",
        "Basis harga (raw/adjusted) dan penanganan aksi korporasi",
        "Batas rate, kuota, dan biaya paket",
        "Lisensi penggunaan/redistribusi data (tampil di grup Telegram)",
    )
