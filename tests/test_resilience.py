from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from data.providers.base import Timeframe
from data.resilience import (
    BreakerState,
    CircuitBreaker,
    RateLimitExceededError,
    TokenBucketRateLimiter,
    TTLCache,
    make_cache_key,
)


def test_cache_key_includes_every_parameter_and_none() -> None:
    k1 = make_cache_key(symbol="BBCA", timeframe=Timeframe.D1, start=None, end=None)
    k2 = make_cache_key(
        symbol="BBCA", timeframe=Timeframe.D1, start=datetime(2026, 1, 1, tzinfo=UTC), end=None
    )
    k3 = make_cache_key(symbol="BBCA", timeframe=Timeframe.H1, start=None, end=None)
    assert k1 != k2 and k1 != k3
    assert k1 == make_cache_key(end=None, start=None, timeframe=Timeframe.D1, symbol="BBCA")
    assert make_cache_key(x="1") != make_cache_key(x=1)


def test_ttl_cache_expiry_and_lru(fake_clock) -> None:
    cache: TTLCache[str] = TTLCache(10, max_entries=2, monotonic=fake_clock.monotonic)
    cache.set("a", "1")
    assert cache.get("a") == "1"
    fake_clock.advance(9.9)
    assert cache.get("a") == "1"
    fake_clock.advance(0.2)
    assert cache.get("a") is None
    cache.set("b", "2")
    cache.set("c", "3")
    cache.set("d", "4")
    assert cache.get("b") is None and cache.get("d") == "4" and len(cache) == 2


def test_zero_ttl_disables_cache(fake_clock) -> None:
    cache: TTLCache[str] = TTLCache(0, monotonic=fake_clock.monotonic)
    cache.set("a", "1")
    assert cache.get("a") is None


async def test_rate_limiter_burst_then_wait(fake_clock) -> None:
    limiter = TokenBucketRateLimiter(
        60, burst=2, monotonic=fake_clock.monotonic, sleep=fake_clock.sleep
    )
    assert await limiter.acquire() == 0.0
    assert await limiter.acquire() == 0.0
    waited = await limiter.acquire()
    assert waited == pytest.approx(1.0)  # 60/menit => 1 token per detik
    assert fake_clock.sleeps == [pytest.approx(1.0)]


async def test_rate_limiter_respects_max_wait(fake_clock) -> None:
    limiter = TokenBucketRateLimiter(2, monotonic=fake_clock.monotonic, sleep=fake_clock.sleep)
    await limiter.acquire()
    await limiter.acquire()
    with pytest.raises(RateLimitExceededError):
        await limiter.acquire(max_wait=5)
    assert fake_clock.sleeps == []


def test_circuit_breaker_transitions(fake_clock) -> None:
    breaker = CircuitBreaker(
        failure_threshold=2, cooldown_seconds=30, monotonic=fake_clock.monotonic
    )
    assert breaker.state is BreakerState.CLOSED and breaker.allow_request()
    breaker.record_failure()
    assert breaker.state is BreakerState.CLOSED
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow_request() is False
    fake_clock.advance(29)
    assert breaker.allow_request() is False
    fake_clock.advance(2)
    assert breaker.state is BreakerState.HALF_OPEN
    assert breaker.allow_request() is True  # satu probe
    assert breaker.allow_request() is False  # probe kedua ditolak
    breaker.record_failure()
    assert breaker.state is BreakerState.OPEN
    fake_clock.advance(31)
    assert breaker.allow_request() is True
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED and breaker.consecutive_failures == 0


async def test_rate_limiter_is_safe_under_concurrency(fake_clock) -> None:
    limiter = TokenBucketRateLimiter(
        600, burst=3, monotonic=fake_clock.monotonic, sleep=fake_clock.sleep
    )
    waits = await asyncio.gather(*(limiter.acquire() for _ in range(5)))
    assert sum(1 for w in waits if w == 0.0) == 3
    assert len(fake_clock.sleeps) == 2
