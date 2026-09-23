"""Primitif ketahanan: TTL cache, rate limiter token-bucket, circuit breaker.

Semua menerima ``clock``/``sleep`` injeksi agar deterministik dalam test.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum, StrEnum
from typing import Any


class RateLimitExceededError(RuntimeError):
    pass


def make_cache_key(**params: Any) -> tuple[tuple[str, str], ...]:
    """Kunci deterministik dari seluruh parameter permintaan (None disertakan secara eksplisit)."""

    def render(value: Any) -> str:
        if isinstance(value, Enum):
            return f"{type(value).__name__}:{value.value}"
        if isinstance(value, datetime | date):
            return value.isoformat()
        if value is None:
            return "None"
        return f"{type(value).__name__}:{value!r}"

    return tuple((k, render(v)) for k, v in sorted(params.items()))


class TTLCache[V]:
    def __init__(
        self,
        ttl_seconds: float,
        *,
        max_entries: int = 4096,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds tidak boleh negatif")
        self._ttl = ttl_seconds
        self._max = max_entries
        self._now = monotonic
        self._store: OrderedDict[Any, tuple[float, V]] = OrderedDict()

    def get(self, key: Any) -> V | None:
        if self._ttl == 0:
            return None
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._now() >= expires_at:
            del self._store[key]
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: Any, value: V) -> None:
        if self._ttl == 0:
            return
        self._store[key] = (self._now() + self._ttl, value)
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def invalidate(self, key: Any) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class TokenBucketRateLimiter:
    def __init__(
        self,
        rate_per_minute: float,
        *,
        burst: int | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute harus > 0")
        self._rate = rate_per_minute / 60.0
        self._capacity = float(burst if burst is not None else max(1, int(rate_per_minute)))
        self._tokens = self._capacity
        self._now = monotonic
        self._sleep = sleep
        self._last = self._now()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._now()
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now

    @property
    def tokens(self) -> float:
        self._refill()
        return self._tokens

    async def acquire(self, *, max_wait: float | None = None) -> float:
        """Ambil satu token; kembalikan lama tunggu. Lempar RateLimitExceededError bila melebihi max_wait."""
        async with self._lock:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return 0.0
            wait = (1 - self._tokens) / self._rate
            if max_wait is not None and wait > max_wait:
                raise RateLimitExceededError(f"harus menunggu {wait:.1f}s > batas {max_wait:.1f}s")
            await self._sleep(wait)
            self._refill()
            self._tokens = max(0.0, self._tokens - 1)
            return wait


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int
    cooldown_seconds: float
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold minimal 1")
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        if (
            self._state is BreakerState.OPEN
            and self.monotonic() >= self._opened_at + self.cooldown_seconds
        ):
            self._state = BreakerState.HALF_OPEN
            self._probe_in_flight = False
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._failures

    def allow_request(self) -> bool:
        state = self.state
        if state is BreakerState.CLOSED:
            return True
        if state is BreakerState.HALF_OPEN and not self._probe_in_flight:
            self._probe_in_flight = True  # hanya satu permintaan percobaan
            return True
        return False

    def record_success(self) -> None:
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._probe_in_flight = False

    def record_failure(self) -> None:
        self._failures += 1
        if self._state is BreakerState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = BreakerState.OPEN
            self._opened_at = self.monotonic()
            self._probe_in_flight = False
