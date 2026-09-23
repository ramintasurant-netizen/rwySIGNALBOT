"""Registry provider data pasar."""

from __future__ import annotations

from collections.abc import Sequence

from config.market_rules import MarketRules
from config.settings import UNVERIFIED_PROVIDERS, Settings
from data.providers.base import MarketDataProvider, ProviderConfigurationError
from data.providers.broker_x import BrokerXProvider
from data.providers.goapi import GoAPIProvider
from data.providers.local_flow import LocalFlowProvider
from data.providers.sectors import SectorsProvider
from data.providers.yahoo import YahooFinanceProvider

TEMPLATE_PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "goapi": GoAPIProvider,
    "sectors": SectorsProvider,
    "broker_x": BrokerXProvider,
}


def build_providers(
    settings: Settings, rules: MarketRules | None = None
) -> Sequence[MarketDataProvider]:
    """Bangun provider aktif sesuai PROVIDER_PRIORITY. Template nonaktif tidak pernah dibangun aktif."""
    providers: list[MarketDataProvider] = []
    for name in settings.provider_order:
        if name == "yahoo":
            providers.append(
                YahooFinanceProvider(
                    rules=rules,
                    max_concurrency=settings.data_max_concurrency,
                    timeout_seconds=settings.data_request_timeout_seconds,
                )
            )
        elif name == "local_flow":
            if settings.flow_csv_dir is None:  # pragma: no cover - dicegah validasi Settings
                raise ProviderConfigurationError("local_flow membutuhkan FLOW_CSV_DIR")
            providers.append(LocalFlowProvider(settings.flow_csv_dir))
        elif name in UNVERIFIED_PROVIDERS:
            raise ProviderConfigurationError(
                f"provider {name!r} adalah template nonaktif: kontrak API belum diverifikasi "
                "(docs/verification_required.md)"
            )
        else:  # pragma: no cover - dicegah oleh validasi Settings
            raise ProviderConfigurationError(f"provider tidak dikenal: {name!r}")
    return providers


__all__ = ["TEMPLATE_PROVIDERS", "build_providers"]
