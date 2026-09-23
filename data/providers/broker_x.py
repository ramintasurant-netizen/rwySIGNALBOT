"""Template adapter broker READ-ONLY — NONAKTIF sampai kontrak API diverifikasi.

Kebijakan tetap: adapter ini hanya akan membaca data pasar/broker summary. Tidak akan pernah
ada endpoint order, portofolio, atau dana. Kredensial broker tidak boleh disimpan di repo.
"""

from __future__ import annotations

from typing import ClassVar

from data.providers.base import Capability
from data.providers.templates import UnverifiedProviderTemplate


class BrokerXProvider(UnverifiedProviderTemplate):
    name: ClassVar[str] = "broker_x"
    display_name: ClassVar[str] = "Broker (read-only)"
    planned_capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.OHLCV_INTRADAY, Capability.QUOTE, Capability.BROKER_SUMMARY}
    )
    verification_items: ClassVar[tuple[str, ...]] = (
        "Ketersediaan API publik read-only dan syarat layanan penggunaannya oleh bot",
        "Endpoint quote real-time, OHLCV intraday, dan broker summary (top buyer/seller)",
        "Skema autentikasi yang tidak memberi hak transaksi (scope read-only)",
        "Zona waktu timestamp dan delay data",
        "Batas rate dan biaya",
        "Lisensi redistribusi data ke grup Telegram",
    )
