from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from data.aggregator import AggregatorConfig, MarketDataAggregator
from data.providers.base import (
    Capability,
    DataOrigin,
    Failed,
    ForeignFlow,
    Ok,
    PriceBasis,
    ProviderMeta,
    QualityStatus,
    Timeframe,
    Unavailable,
    Unsupported,
)
from data.resilience import BreakerState
from tests.conftest import FakeClock, FakeProvider, failed, make_frame, make_quote, ok_frame

NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)
SESSION = date(2026, 3, 13)


def _config(**overrides) -> AggregatorConfig:
    base = {
        "timeout_seconds": 1.0,
        "max_retries": 2,
        "retry_backoff_seconds": 0.5,
        "cache_ttl_seconds": 300,
        "breaker_failure_threshold": 2,
        "breaker_cooldown_seconds": 60,
        "rate_limit_per_minute": 600,
        "daily_min_history_bars": 250,
    }
    base.update(overrides)
    return AggregatorConfig(**base)


def _agg(providers, clock: FakeClock, **overrides) -> MarketDataAggregator:
    return MarketDataAggregator(
        providers,
        _config(**overrides),
        clock=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


async def test_single_provider_is_degraded_but_usable_in_development(fake_clock) -> None:
    p = FakeProvider("a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW))])
    result = await _agg([p], fake_clock).get_ohlcv(
        "BBCA", Timeframe.D1, None, None, expected_last_session=SESSION
    )
    assert result.status is QualityStatus.DEGRADED
    assert result.usable is True
    assert result.provider_used == "a" and result.providers_tried == ("a",)
    assert any("tanpa cross-validation" in i for i in result.issues)
    assert result.frame is not None and result.frame.origin is DataOrigin.FIXTURE


async def test_single_provider_not_usable_when_cross_validation_required(fake_clock) -> None:
    p = FakeProvider("a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW))])
    result = await _agg([p], fake_clock, require_cross_validation=True).get_ohlcv(
        "BBCA", Timeframe.D1, None, None
    )
    assert result.status is QualityStatus.DEGRADED
    assert result.usable is False


async def test_two_agreeing_providers_are_ok(fake_clock) -> None:
    a = FakeProvider("a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW))])
    b = FakeProvider("b", ohlcv_results=[ok_frame("b", make_frame(provider="b", now=NOW))])
    result = await _agg([a, b], fake_clock, require_cross_validation=True).get_ohlcv(
        "BBCA", Timeframe.D1, None, None
    )
    assert result.status is QualityStatus.OK and result.usable
    assert result.cross_validation is not None and result.cross_validation.passed
    assert result.providers_tried == ("a", "b")


async def test_disagreeing_providers_block_symbol_as_suspect(fake_clock) -> None:
    a = FakeProvider("a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW))])
    b = FakeProvider("b", ohlcv_results=[ok_frame("b", make_frame(provider="b", now=NOW, seed=9))])
    result = await _agg([a, b], fake_clock).get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert result.status is QualityStatus.SUSPECT
    assert result.frame is None and result.usable is False
    assert any("selisih close" in i for i in result.issues)


async def test_failover_to_second_provider(fake_clock) -> None:
    a = FakeProvider("a", ohlcv_results=[failed("a", retryable=False)])
    b = FakeProvider("b", ohlcv_results=[ok_frame("b", make_frame(provider="b", now=NOW))])
    result = await _agg([a, b], fake_clock).get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert result.provider_used == "b"
    assert result.status is QualityStatus.DEGRADED  # provider gagal tidak bisa jadi pembanding
    assert a.call_count("ohlcv") == 1  # non-retryable: tidak diulang
    assert result.providers_tried == ("a", "b")


async def test_retry_only_for_retryable_failures(fake_clock) -> None:
    frame = make_frame(provider="a", now=NOW)
    a = FakeProvider(
        "a",
        ohlcv_results=[
            failed("a", retryable=True),
            failed("a", retryable=True),
            ok_frame("a", frame),
        ],
    )
    result = await _agg([a], fake_clock).get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert result.status is QualityStatus.DEGRADED and result.provider_used == "a"
    assert a.call_count("ohlcv") == 3
    assert len(fake_clock.sleeps) == 2 and all(s > 0 for s in fake_clock.sleeps)


async def test_retries_exhausted_then_missing_without_synthetic_data(fake_clock) -> None:
    a = FakeProvider("a", ohlcv_results=[failed("a", retryable=True)])
    result = await _agg([a], fake_clock, max_retries=1).get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert result.status is QualityStatus.MISSING and result.frame is None
    assert a.call_count("ohlcv") == 2
    assert any("kegagalan fixture" in i for i in result.issues)


async def test_timeout_is_enforced_and_retryable(fake_clock) -> None:
    a = FakeProvider(
        "a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW))], delay=0.2
    )
    result = await _agg([a], fake_clock, timeout_seconds=0.02, max_retries=1).get_ohlcv(
        "BBCA", Timeframe.D1, None, None
    )
    assert result.status is QualityStatus.MISSING
    assert a.call_count("ohlcv") == 2
    assert any("timeout" in i for i in result.issues)


async def test_provider_exception_becomes_failed_not_crash(fake_clock) -> None:
    a = FakeProvider("a", raise_exc=RuntimeError("ledakan provider"))
    result = await _agg([a], fake_clock).get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert result.status is QualityStatus.MISSING
    assert any("RuntimeError" in i for i in result.issues)


async def test_circuit_breaker_skips_failing_provider_then_probes(fake_clock) -> None:
    a = FakeProvider("a", ohlcv_results=[failed("a")])
    b = FakeProvider("b", ohlcv_results=[ok_frame("b", make_frame(provider="b", now=NOW))])
    agg = _agg([a, b], fake_clock, cache_ttl_seconds=0)
    for _ in range(2):
        await agg.get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert agg.breaker_state("a") is BreakerState.OPEN
    calls_before = a.call_count("ohlcv")
    result = await agg.get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert a.call_count("ohlcv") == calls_before  # dilewati
    assert any("circuit breaker" in i for i in result.issues)
    assert result.provider_used == "b"
    fake_clock.advance(61)
    await agg.get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert a.call_count("ohlcv") == calls_before + 1  # satu probe half-open


async def test_cache_hit_avoids_provider_calls_and_keys_on_params(fake_clock) -> None:
    a = FakeProvider("a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW))])
    agg = _agg([a], fake_clock)
    first = await agg.get_ohlcv("BBCA", Timeframe.D1, None, None, expected_last_session=SESSION)
    second = await agg.get_ohlcv("BBCA", Timeframe.D1, None, None, expected_last_session=SESSION)
    assert first.from_cache is False and second.from_cache is True
    assert a.call_count("ohlcv") == 1
    await agg.get_ohlcv("BBCA", Timeframe.D1, None, None, expected_last_session=date(2026, 3, 12))
    assert a.call_count("ohlcv") == 2  # parameter berbeda = kunci berbeda
    fake_clock.advance(301)
    await agg.get_ohlcv("BBCA", Timeframe.D1, None, None, expected_last_session=SESSION)
    assert a.call_count("ohlcv") == 3  # TTL habis


async def test_stale_or_invalid_results_are_not_cached(fake_clock) -> None:
    stale = make_frame(provider="a", now=NOW, end_session=date(2026, 3, 12))
    a = FakeProvider("a", ohlcv_results=[ok_frame("a", stale)])
    agg = _agg([a], fake_clock)
    r1 = await agg.get_ohlcv("BBCA", Timeframe.D1, None, None, expected_last_session=SESSION)
    r2 = await agg.get_ohlcv("BBCA", Timeframe.D1, None, None, expected_last_session=SESSION)
    assert r1.status is QualityStatus.STALE and r2.from_cache is False
    assert a.call_count("ohlcv") == 2


async def test_insufficient_history_falls_over_then_missing(fake_clock) -> None:
    short = FakeProvider(
        "a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW, n=100))]
    )
    result = await _agg([short], fake_clock).get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert result.status is QualityStatus.MISSING
    assert any("histori" in i for i in result.issues)


async def test_unsupported_capability(fake_clock) -> None:
    a = FakeProvider("a", caps=frozenset({Capability.OHLCV_DAILY}))
    result = await _agg([a], fake_clock).get_ohlcv("BBCA", Timeframe.M15, None, None)
    assert result.status is QualityStatus.UNSUPPORTED and result.providers_tried == ()


async def test_adjusted_request_skips_cross_validation(fake_clock) -> None:
    a = FakeProvider(
        "a",
        ohlcv_results=[
            ok_frame("a", make_frame(provider="a", now=NOW, price_basis=PriceBasis.ADJUSTED))
        ],
    )
    b = FakeProvider("b", ohlcv_results=[ok_frame("b", make_frame(provider="b", now=NOW))])
    result = await _agg([a, b], fake_clock).get_ohlcv(
        "BBCA", Timeframe.D1, None, None, price_basis=PriceBasis.ADJUSTED
    )
    assert result.status is QualityStatus.DEGRADED
    assert b.call_count("ohlcv") == 0
    assert any("hanya dilakukan untuk basis harga raw" in i for i in result.issues)


async def test_rate_limit_exceeded_is_reported_as_failed(fake_clock) -> None:
    a = FakeProvider("a", ohlcv_results=[ok_frame("a", make_frame(provider="a", now=NOW))])
    agg = _agg([a], fake_clock, rate_limit_per_minute=1, cache_ttl_seconds=0, timeout_seconds=1.0)
    first = await agg.get_ohlcv("BBCA", Timeframe.D1, None, None)
    second = await agg.get_ohlcv("BBRI", Timeframe.D1, None, None)
    assert first.status is QualityStatus.DEGRADED
    assert second.status is QualityStatus.MISSING and any("rate limit" in i for i in second.issues)
    assert a.call_count("ohlcv") == 1


# ----------------------------------------------------------------------------- quote


async def test_quote_single_provider_degraded(fake_clock) -> None:
    a = FakeProvider("a", quote_results=[make_quote("a", market_time=NOW - timedelta(minutes=3))])
    result = await _agg([a], fake_clock).get_quote("BBCA")
    assert result.status is QualityStatus.DEGRADED and result.usable
    assert result.quote is not None and result.quote.price == Decimal("9450")


async def test_quote_stale_is_rejected(fake_clock) -> None:
    a = FakeProvider("a", quote_results=[make_quote("a", market_time=NOW - timedelta(hours=3))])
    result = await _agg([a], fake_clock).get_quote("BBCA")
    assert result.status is QualityStatus.STALE and result.quote is None


async def test_quote_comparison_requires_identical_snapshot_time(fake_clock) -> None:
    t = NOW - timedelta(minutes=2)
    a = FakeProvider("a", quote_results=[make_quote("a", market_time=t)])
    b_same = FakeProvider("b", quote_results=[make_quote("b", price="9460", market_time=t)])
    ok = await _agg([a, b_same], fake_clock).get_quote("BBCA")
    assert ok.status is QualityStatus.OK

    a2 = FakeProvider("a", quote_results=[make_quote("a", market_time=t)])
    b_other_time = FakeProvider(
        "b", quote_results=[make_quote("b", price="9460", market_time=t - timedelta(minutes=1))]
    )
    degraded = await _agg([a2, b_other_time], fake_clock).get_quote("BBCA")
    assert degraded.status is QualityStatus.DEGRADED
    assert any("waktu snapshot berbeda" in i for i in degraded.issues)

    a3 = FakeProvider("a", quote_results=[make_quote("a", market_time=t)])
    b_far = FakeProvider("b", quote_results=[make_quote("b", price="9900", market_time=t)])
    suspect = await _agg([a3, b_far], fake_clock).get_quote("BBCA")
    assert suspect.status is QualityStatus.SUSPECT and suspect.quote is None


# ----------------------------------------------------------------------------- data opsional


async def test_foreign_flow_distinguishes_unsupported_unavailable_and_legit_zero(
    fake_clock,
) -> None:
    none_cap = FakeProvider("a")
    result = await _agg([none_cap], fake_clock).get_foreign_flow("BBCA", SESSION)
    assert isinstance(result, Unsupported)

    zero = ForeignFlow("BBCA", SESSION, Decimal("0"), "b", NOW, origin=DataOrigin.FIXTURE)
    with_zero = FakeProvider(
        "b",
        caps=frozenset({Capability.FOREIGN_FLOW}),
        foreign_flow_result=Ok(zero, ProviderMeta("b", NOW, None, DataOrigin.FIXTURE)),
    )
    ok = await _agg([with_zero], fake_clock).get_foreign_flow("BBCA", SESSION)
    assert isinstance(ok, Ok) and ok.value.net_value == Decimal("0")

    unavailable = FakeProvider(
        "c",
        caps=frozenset({Capability.FOREIGN_FLOW}),
        foreign_flow_result=Unavailable("c", "belum dipublikasikan"),
    )
    res = await _agg([unavailable], fake_clock).get_foreign_flow("BBCA", SESSION)
    assert isinstance(res, Unavailable)

    fail = FakeProvider(
        "d", caps=frozenset({Capability.FOREIGN_FLOW}), foreign_flow_result=Failed("d", "rusak")
    )
    res2 = await _agg([fail], fake_clock).get_foreign_flow("BBCA", SESSION)
    assert isinstance(res2, Failed)


async def test_health_reports_breaker_state(fake_clock) -> None:
    a = FakeProvider("a", healthy=False)
    b = FakeProvider("b", healthy=True)
    health = await _agg([a, b], fake_clock).health()
    assert [(h.provider, h.healthy, h.breaker_state) for h in health] == [
        ("a", False, BreakerState.CLOSED),
        ("b", True, BreakerState.CLOSED),
    ]


def test_aggregator_rejects_duplicate_or_empty_providers(fake_clock) -> None:
    with pytest.raises(ValueError):
        _agg([], fake_clock)
    with pytest.raises(ValueError):
        _agg([FakeProvider("a"), FakeProvider("a")], fake_clock)


async def test_concurrent_requests_share_semantics(fake_clock) -> None:
    a = FakeProvider(
        "a", ohlcv_results=lambda *args: ok_frame("a", make_frame(provider="a", now=NOW))
    )
    agg = _agg([a], fake_clock)
    results = await asyncio.gather(
        *(agg.get_ohlcv(s, Timeframe.D1, None, None) for s in ("BBCA", "BBRI", "TLKM"))
    )
    assert all(r.status is QualityStatus.DEGRADED for r in results)
    assert a.call_count("ohlcv") == 3
