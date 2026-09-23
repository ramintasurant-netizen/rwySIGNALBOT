from __future__ import annotations

from datetime import date

import pytest

from data.providers import TEMPLATE_PROVIDERS, build_providers
from data.providers.base import Capability, ProviderConfigurationError, Timeframe, Unsupported
from data.providers.broker_x import BrokerXProvider
from data.providers.goapi import GoAPIProvider
from data.providers.sectors import SectorsProvider
from data.providers.yahoo import YahooFinanceProvider


@pytest.mark.parametrize("cls", [GoAPIProvider, SectorsProvider, BrokerXProvider])
async def test_templates_are_inert(cls) -> None:
    provider = cls()
    assert provider.capabilities == frozenset()
    assert provider.contract_verified is False
    assert provider.verification_items
    for result in (
        await provider.get_ohlcv("BBCA", Timeframe.D1, None, None),
        await provider.get_quote("BBCA"),
        await provider.get_foreign_flow("BBCA", date(2026, 3, 13)),
        await provider.get_broker_summary("BBCA", date(2026, 3, 13)),
    ):
        assert isinstance(result, Unsupported)
        assert "belum diverifikasi" in result.reason
    assert await provider.health_check() is False


def test_broker_template_never_plans_order_capabilities() -> None:
    assert BrokerXProvider.planned_capabilities <= set(Capability)
    assert not any("order" in c.value for c in BrokerXProvider.planned_capabilities)


def test_registry_builds_only_yahoo_by_default(settings_factory, market_rules) -> None:
    providers = build_providers(settings_factory(), market_rules)
    assert [p.name for p in providers] == ["yahoo"]
    assert isinstance(providers[0], YahooFinanceProvider)


def test_registry_refuses_templates(settings_factory) -> None:
    settings = settings_factory()
    object.__setattr__(settings, "_provider_order", ("yahoo", "goapi"))
    with pytest.raises(ProviderConfigurationError, match="template nonaktif"):
        build_providers(settings)
    assert set(TEMPLATE_PROVIDERS) == {"goapi", "sectors", "broker_x"}
