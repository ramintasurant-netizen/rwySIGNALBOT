"""Template provider yang kontrak API-nya BELUM diverifikasi.

Template ini sengaja nonaktif: tidak menebak endpoint, field response, autentikasi, atau
batas rate. Semua metode mengembalikan ``Unsupported`` dan ``health_check`` bernilai False.
Mengaktifkannya lewat konfigurasi ditolak oleh ``config.settings``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import ClassVar

from data.providers.base import (
    BrokerSummary,
    Capability,
    ForeignFlow,
    MarketDataProvider,
    OHLCVFrame,
    PriceBasis,
    ProviderResult,
    Quote,
    Timeframe,
    Unsupported,
)


class UnverifiedProviderTemplate(MarketDataProvider):
    name: ClassVar[str] = "unverified"
    display_name: ClassVar[str] = "Provider belum diverifikasi"
    capabilities: ClassVar[frozenset[Capability]] = frozenset()
    # Kemampuan yang DIHARAPKAN setelah kontrak diverifikasi; belum aktif.
    planned_capabilities: ClassVar[frozenset[Capability]] = frozenset()
    verification_items: ClassVar[tuple[str, ...]] = ()

    @property
    def contract_verified(self) -> bool:
        return False

    def _unsupported(self) -> Unsupported:
        return Unsupported(
            self.name,
            f"adapter {self.display_name} nonaktif: kontrak API belum diverifikasi "
            "(docs/verification_required.md)",
        )

    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
        *,
        price_basis: PriceBasis = PriceBasis.RAW,
    ) -> ProviderResult[OHLCVFrame]:
        return self._unsupported()

    async def get_quote(self, symbol: str) -> ProviderResult[Quote]:
        return self._unsupported()

    async def get_foreign_flow(
        self, symbol: str, session_date: date
    ) -> ProviderResult[ForeignFlow]:
        return self._unsupported()

    async def get_broker_summary(
        self, symbol: str, session_date: date
    ) -> ProviderResult[BrokerSummary]:
        return self._unsupported()

    async def health_check(self) -> bool:
        return False
