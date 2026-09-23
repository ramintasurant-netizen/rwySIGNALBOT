"""Aggregator data pasar: prioritas provider, failover, timeout, retry, breaker, rate limit,
TTL cache, validasi kualitas, dan cross-validation.

Prinsip: tidak ada data sintetis sebagai fallback. Jika semua provider gagal, hasilnya
``MISSING``/``STALE`` dengan daftar alasan — bukan frame kosong yang tampak valid.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_result,
    stop_after_attempt,
    wait_exponential_jitter,
)

from config.settings import Settings
from core.timeutil import to_utc, utc_now
from data.providers.base import (
    BrokerSummary,
    Capability,
    Failed,
    ForeignFlow,
    MarketDataProvider,
    OHLCVFrame,
    Ok,
    PriceBasis,
    ProviderResult,
    QualityStatus,
    Quote,
    Timeframe,
    Unavailable,
    Unsupported,
)
from data.resilience import (
    BreakerState,
    CircuitBreaker,
    RateLimitExceededError,
    TokenBucketRateLimiter,
    TTLCache,
    make_cache_key,
)
from data.validation import (
    CrossValidation,
    ValidationReport,
    cross_validate_close,
    validate_ohlcv,
    validate_quote,
)


@dataclass(frozen=True, slots=True)
class AggregatorConfig:
    timeout_seconds: float = 20.0
    max_retries: int = 2
    retry_backoff_seconds: float = 1.0
    cache_ttl_seconds: float = 300.0
    quote_cache_ttl_seconds: float = 30.0
    breaker_failure_threshold: int = 3
    breaker_cooldown_seconds: float = 120.0
    rate_limit_per_minute: float = 60.0
    cross_validation_max_diff_pct: Decimal = Decimal("0.5")
    require_cross_validation: bool = False
    intraday_max_age: timedelta = timedelta(minutes=20)
    daily_min_history_bars: int = 250

    @classmethod
    def from_settings(cls, settings: Settings) -> AggregatorConfig:
        return cls(
            timeout_seconds=settings.data_request_timeout_seconds,
            max_retries=settings.data_max_retries,
            retry_backoff_seconds=settings.data_retry_backoff_seconds,
            cache_ttl_seconds=settings.data_cache_ttl_seconds,
            breaker_failure_threshold=settings.data_breaker_failure_threshold,
            breaker_cooldown_seconds=settings.data_breaker_cooldown_seconds,
            rate_limit_per_minute=settings.data_rate_limit_per_minute,
            cross_validation_max_diff_pct=settings.cross_validation_max_diff_pct,
            require_cross_validation=settings.requires_cross_validation,
            intraday_max_age=timedelta(minutes=settings.intraday_max_age_minutes),
            daily_min_history_bars=settings.daily_min_history_bars,
        )


@dataclass(frozen=True, slots=True)
class AggregatedOHLCV:
    symbol: str
    timeframe: Timeframe
    status: QualityStatus
    frame: OHLCVFrame | None
    provider_used: str | None
    providers_tried: tuple[str, ...]
    issues: tuple[str, ...]
    validation: ValidationReport | None = None
    cross_validation: CrossValidation | None = None
    from_cache: bool = False
    degraded_allowed: bool = True

    @property
    def usable(self) -> bool:
        if self.frame is None:
            return False
        if self.status is QualityStatus.OK:
            return True
        return self.status is QualityStatus.DEGRADED and self.degraded_allowed


@dataclass(frozen=True, slots=True)
class AggregatedQuote:
    symbol: str
    status: QualityStatus
    quote: Quote | None
    provider_used: str | None
    providers_tried: tuple[str, ...]
    issues: tuple[str, ...]
    from_cache: bool = False
    degraded_allowed: bool = True

    @property
    def usable(self) -> bool:
        if self.quote is None:
            return False
        if self.status is QualityStatus.OK:
            return True
        return self.status is QualityStatus.DEGRADED and self.degraded_allowed


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: str
    healthy: bool
    breaker_state: BreakerState
    consecutive_failures: int
    checked_at: datetime


@dataclass
class _ProviderSlot:
    provider: MarketDataProvider
    breaker: CircuitBreaker
    limiter: TokenBucketRateLimiter
    issues: list[str] = field(default_factory=list)


def _describe(result: ProviderResult[Any]) -> str:
    if isinstance(result, Unsupported | Unavailable):
        return result.reason
    if isinstance(result, Failed):
        return f"gagal — {result.error}"
    return "ok"


class MarketDataAggregator:
    def __init__(
        self,
        providers: Sequence[MarketDataProvider],
        config: AggregatorConfig | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not providers:
            raise ValueError("aggregator membutuhkan minimal satu provider")
        names = [p.name for p in providers]
        if len(names) != len(set(names)):
            raise ValueError("nama provider duplikat")
        self._config = config or AggregatorConfig()
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._slots: list[_ProviderSlot] = [
            _ProviderSlot(
                provider=p,
                breaker=CircuitBreaker(
                    self._config.breaker_failure_threshold,
                    self._config.breaker_cooldown_seconds,
                    monotonic=monotonic,
                ),
                limiter=TokenBucketRateLimiter(
                    self._config.rate_limit_per_minute, monotonic=monotonic, sleep=sleep
                ),
            )
            for p in providers
        ]
        self._ohlcv_cache: TTLCache[AggregatedOHLCV] = TTLCache(
            self._config.cache_ttl_seconds, monotonic=monotonic
        )
        self._quote_cache: TTLCache[AggregatedQuote] = TTLCache(
            self._config.quote_cache_ttl_seconds, monotonic=monotonic
        )

    # ------------------------------------------------------------------ util
    @property
    def config(self) -> AggregatorConfig:
        return self._config

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(s.provider.name for s in self._slots)

    def _slots_for(self, capability: Capability) -> list[_ProviderSlot]:
        return [s for s in self._slots if s.provider.supports(capability)]

    def breaker_state(self, provider_name: str) -> BreakerState:
        for slot in self._slots:
            if slot.provider.name == provider_name:
                return slot.breaker.state
        raise KeyError(provider_name)

    async def _call(
        self,
        slot: _ProviderSlot,
        operation: Callable[[], Awaitable[ProviderResult[Any]]],
        label: str,
    ) -> ProviderResult[Any]:
        """Satu operasi provider dengan breaker, rate limit, timeout, dan retry terbatas.

        Retry hanya untuk ``Failed(retryable=True)``/timeout. ``Unavailable``/``Unsupported``
        adalah jawaban sah provider: tidak diulang dan tidak membuka breaker.
        """
        name = slot.provider.name
        if not slot.breaker.allow_request():
            return Failed(name, f"circuit breaker {slot.breaker.state.value}; permintaan dilewati")
        try:
            await slot.limiter.acquire(max_wait=self._config.timeout_seconds)
        except RateLimitExceededError as exc:
            return Failed(name, f"rate limit: {exc}")

        async def attempt() -> ProviderResult[Any]:
            try:
                return await asyncio.wait_for(operation(), timeout=self._config.timeout_seconds)
            except TimeoutError:
                return Failed(
                    name, f"timeout {self._config.timeout_seconds}s pada {label}", retryable=True
                )
            except Exception as exc:  # noqa: BLE001 - provider yang melempar = gagal, bukan crash job
                return Failed(name, f"{type(exc).__name__}: {exc}")

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._config.max_retries + 1),
            wait=wait_exponential_jitter(initial=self._config.retry_backoff_seconds, max=30),
            retry=retry_if_result(lambda r: isinstance(r, Failed) and r.retryable),
            sleep=self._sleep,
        )
        try:
            result: ProviderResult[Any] = await retrying(attempt)
        except RetryError as exc:
            result = exc.last_attempt.result()

        if isinstance(result, Failed):
            slot.breaker.record_failure()
            logger.warning("provider {} gagal pada {}: {}", name, label, result.error)
        else:
            slot.breaker.record_success()
        return result

    # ------------------------------------------------------------------ OHLCV
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime | None,
        end: datetime | None,
        *,
        price_basis: PriceBasis = PriceBasis.RAW,
        expected_last_session: date | None = None,
        min_bars: int | None = None,
    ) -> AggregatedOHLCV:
        if min_bars is not None:
            min_required = min_bars
        elif timeframe is Timeframe.D1:
            min_required = self._config.daily_min_history_bars
        else:
            min_required = 1
        key = make_cache_key(
            op="ohlcv",
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            price_basis=price_basis,
            expected_last_session=expected_last_session,
            min_bars=min_required,
        )
        cached = self._ohlcv_cache.get(key)
        if cached is not None:
            return replace(cached, from_cache=True)

        slots = self._slots_for(timeframe.capability)
        if not slots:
            return AggregatedOHLCV(
                symbol,
                timeframe,
                QualityStatus.UNSUPPORTED,
                None,
                None,
                (),
                (f"tidak ada provider yang mendukung {timeframe.capability.value}",),
            )

        tried: list[str] = []
        issues: list[str] = []
        primary: OHLCVFrame | None = None
        primary_slot: _ProviderSlot | None = None
        primary_report: ValidationReport | None = None
        saw_stale = False
        now = self._clock()

        for slot in slots:
            name = slot.provider.name
            tried.append(name)
            result = await self._call(
                slot,
                lambda s=slot: s.provider.get_ohlcv(
                    symbol, timeframe, start, end, price_basis=price_basis
                ),
                f"ohlcv {symbol} {timeframe.value}",
            )
            if isinstance(result, Ok):
                report = validate_ohlcv(
                    result.value,
                    min_bars=min_required,
                    expected_last_session=expected_last_session,
                    now=now,
                    max_age=self._config.intraday_max_age,
                )
                if report.ok:
                    primary, primary_slot, primary_report = result.value, slot, report
                    break
                saw_stale = saw_stale or report.status is QualityStatus.STALE
                issues.append(f"{name}: {report.status.value} — " + "; ".join(report.issues))
            else:
                issues.append(f"{name}: {_describe(result)}")

        if primary is None or primary_slot is None:
            status = QualityStatus.STALE if saw_stale else QualityStatus.MISSING
            return AggregatedOHLCV(
                symbol, timeframe, status, None, None, tuple(tried), tuple(issues)
            )

        cross: CrossValidation | None = None
        if price_basis is PriceBasis.RAW:
            # Provider yang sudah gagal/tidak valid sebelumnya tidak dipakai sebagai pembanding.
            candidates = [
                s for s in slots if s is not primary_slot and s.provider.name not in tried
            ]
            for slot in candidates:
                tried.append(slot.provider.name)
                result = await self._call(
                    slot,
                    lambda s=slot: s.provider.get_ohlcv(
                        symbol, timeframe, start, end, price_basis=PriceBasis.RAW
                    ),
                    f"cross-validation {symbol}",
                )
                if not isinstance(result, Ok):
                    issues.append(
                        f"cross-validation {slot.provider.name} tidak tersedia: {_describe(result)}"
                    )
                    continue
                candidate = cross_validate_close(
                    primary, result.value, max_diff_pct=self._config.cross_validation_max_diff_pct
                )
                if candidate.status == "not_comparable":
                    issues.append(f"cross-validation {slot.provider.name}: {candidate.reason}")
                    continue
                cross = candidate
                break
        else:
            issues.append("cross-validation hanya dilakukan untuk basis harga raw")

        if cross is not None and cross.status == "suspect":
            issues.append(cross.reason)
            return AggregatedOHLCV(
                symbol,
                timeframe,
                QualityStatus.SUSPECT,
                None,
                primary.provider,
                tuple(tried),
                tuple(issues),
                validation=primary_report,
                cross_validation=cross,
            )

        if cross is not None and cross.status == "ok":
            status = QualityStatus.OK
        else:
            status = QualityStatus.DEGRADED
            issues.append("tanpa cross-validation antar-provider (satu sumber data)")

        outcome = AggregatedOHLCV(
            symbol,
            timeframe,
            status,
            primary,
            primary.provider,
            tuple(tried),
            tuple(issues),
            validation=primary_report,
            cross_validation=cross,
            degraded_allowed=not self._config.require_cross_validation,
        )
        self._ohlcv_cache.set(key, outcome)
        return outcome

    # ------------------------------------------------------------------ Quote
    async def get_quote(self, symbol: str) -> AggregatedQuote:
        key = make_cache_key(op="quote", symbol=symbol)
        cached = self._quote_cache.get(key)
        if cached is not None:
            return replace(cached, from_cache=True)

        slots = self._slots_for(Capability.QUOTE)
        if not slots:
            return AggregatedQuote(
                symbol, QualityStatus.UNSUPPORTED, None, None, (), ("tidak ada provider quote",)
            )

        tried: list[str] = []
        issues: list[str] = []
        saw_stale = False
        now = self._clock()
        primary: Quote | None = None
        primary_slot: _ProviderSlot | None = None
        for slot in slots:
            name = slot.provider.name
            tried.append(name)
            result = await self._call(
                slot, lambda s=slot: s.provider.get_quote(symbol), f"quote {symbol}"
            )
            if isinstance(result, Ok):
                report = validate_quote(
                    result.value, now=now, max_age=self._config.intraday_max_age
                )
                if report.ok:
                    primary, primary_slot = result.value, slot
                    break
                saw_stale = saw_stale or report.status is QualityStatus.STALE
                issues.append(f"{name}: {report.status.value} — " + "; ".join(report.issues))
            else:
                issues.append(f"{name}: {_describe(result)}")

        if primary is None or primary_slot is None:
            status = QualityStatus.STALE if saw_stale else QualityStatus.MISSING
            return AggregatedQuote(symbol, status, None, None, tuple(tried), tuple(issues))

        # Pembanding hanya sah bila snapshot bermakna sama (market_time identik).
        status = QualityStatus.DEGRADED
        candidates = [s for s in slots if s is not primary_slot and s.provider.name not in tried]
        for slot in candidates:
            tried.append(slot.provider.name)
            result = await self._call(
                slot, lambda s=slot: s.provider.get_quote(symbol), f"quote pembanding {symbol}"
            )
            if not isinstance(result, Ok):
                issues.append(
                    f"pembanding {slot.provider.name} tidak tersedia: {_describe(result)}"
                )
                continue
            other: Quote = result.value
            if to_utc(other.market_time) != to_utc(primary.market_time):
                issues.append(
                    f"pembanding {slot.provider.name}: waktu snapshot berbeda, tidak dibandingkan"
                )
                continue
            if other.price == 0:
                continue
            diff = (abs(primary.price - other.price) / other.price * 100).quantize(
                Decimal("0.0001")
            )
            if diff > self._config.cross_validation_max_diff_pct:
                issues.append(
                    f"selisih quote {diff}% > {self._config.cross_validation_max_diff_pct}% "
                    f"vs {slot.provider.name}"
                )
                return AggregatedQuote(
                    symbol,
                    QualityStatus.SUSPECT,
                    None,
                    primary.provider,
                    tuple(tried),
                    tuple(issues),
                )
            status = QualityStatus.OK
            break
        if status is QualityStatus.DEGRADED:
            issues.append("quote tanpa pembanding yang sebanding (satu sumber data)")

        outcome = AggregatedQuote(
            symbol,
            status,
            primary,
            primary.provider,
            tuple(tried),
            tuple(issues),
            degraded_allowed=not self._config.require_cross_validation,
        )
        self._quote_cache.set(key, outcome)
        return outcome

    # ------------------------------------------------------------------ data opsional
    async def get_foreign_flow(
        self, symbol: str, session_date: date
    ) -> ProviderResult[ForeignFlow]:
        return await self._first_ok(
            Capability.FOREIGN_FLOW,
            lambda s: s.provider.get_foreign_flow(symbol, session_date),
            f"foreign flow {symbol} {session_date}",
        )

    async def get_broker_summary(
        self, symbol: str, session_date: date
    ) -> ProviderResult[BrokerSummary]:
        return await self._first_ok(
            Capability.BROKER_SUMMARY,
            lambda s: s.provider.get_broker_summary(symbol, session_date),
            f"broker summary {symbol} {session_date}",
        )

    async def _first_ok(
        self,
        capability: Capability,
        operation: Callable[[_ProviderSlot], Awaitable[ProviderResult[Any]]],
        label: str,
    ) -> ProviderResult[Any]:
        """Failover pass-through untuk data opsional; hasil tetap tagged union (nol sah ⊂ Ok)."""
        slots = self._slots_for(capability)
        if not slots:
            return Unsupported(
                "aggregator", f"tidak ada provider aktif yang mendukung {capability.value}"
            )
        unavailable: Unavailable | None = None
        failed: Failed | None = None
        for slot in slots:
            result = await self._call(slot, lambda s=slot: operation(s), label)
            if isinstance(result, Ok):
                return result
            if isinstance(result, Unavailable):
                unavailable = unavailable or result
            elif isinstance(result, Failed):
                failed = failed or result
        if unavailable is not None:
            return unavailable
        if failed is not None:
            return failed
        return Unsupported(
            "aggregator", f"semua provider menyatakan {capability.value} tidak didukung"
        )

    # ------------------------------------------------------------------ health
    async def health(self) -> tuple[ProviderHealth, ...]:
        out: list[ProviderHealth] = []
        for slot in self._slots:
            try:
                healthy = await asyncio.wait_for(
                    slot.provider.health_check(), timeout=self._config.timeout_seconds
                )
            except Exception:  # noqa: BLE001
                healthy = False
            out.append(
                ProviderHealth(
                    provider=slot.provider.name,
                    healthy=healthy,
                    breaker_state=slot.breaker.state,
                    consecutive_failures=slot.breaker.consecutive_failures,
                    checked_at=self._clock(),
                )
            )
        return tuple(out)
