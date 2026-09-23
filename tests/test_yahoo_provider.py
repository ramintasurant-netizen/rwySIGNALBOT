from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from data.providers.base import Failed, Ok, PriceBasis, Timeframe, Unavailable
from data.providers.yahoo import YahooFinanceProvider, to_yahoo_ticker
from tests.conftest import make_yf_frame

NOW = datetime(2026, 3, 16, 3, 0, tzinfo=UTC)  # Senin 10:00 WIB


def _downloader_returning(frame: pd.DataFrame):
    calls: list[dict] = []

    def download(ticker, *, interval, start, end, period):
        calls.append(
            {"ticker": ticker, "interval": interval, "start": start, "end": end, "period": period}
        )
        return frame

    download.calls = calls  # type: ignore[attr-defined]
    return download


@pytest.mark.parametrize("raw", ["bbca", "BBCA", "BBCA.JK", " bbca.jk "])
def test_ticker_suffix_mapping(raw: str) -> None:
    assert to_yahoo_ticker(raw) == "BBCA.JK"


async def test_get_ohlcv_normalizes_and_labels(market_rules) -> None:
    dl = _downloader_returning(make_yf_frame(300, end_session=date(2026, 3, 16)))
    provider = YahooFinanceProvider(rules=market_rules, downloader=dl, clock=lambda: NOW)
    result = await provider.get_ohlcv("bbca", Timeframe.D1, None, None)
    assert isinstance(result, Ok)
    frame = result.value
    assert (
        frame.symbol == "BBCA" and frame.provider == "yahoo" and frame.price_basis is PriceBasis.RAW
    )
    assert dl.calls[0]["ticker"] == "BBCA.JK" and dl.calls[0]["interval"] == "1d"
    assert dl.calls[0]["period"] == "2y"  # tanpa start/end: histori default cukup untuk warmup
    assert str(frame.frame.index.tz) == "UTC"
    assert (
        frame.frame["complete"].iloc[-1] is False or bool(frame.frame["complete"].iloc[-1]) is False
    )
    assert frame.last_complete_session == date(2026, 3, 13)
    assert any("Yahoo" in n for n in frame.notes)
    assert result.meta.market_time == frame.last_bar_time


async def test_adjusted_and_raw_are_distinct() -> None:
    dl = _downloader_returning(make_yf_frame(50))
    provider = YahooFinanceProvider(downloader=dl, clock=lambda: NOW)
    raw = await provider.get_ohlcv("BBCA", Timeframe.D1, None, None)
    adj = await provider.get_ohlcv(
        "BBCA", Timeframe.D1, None, None, price_basis=PriceBasis.ADJUSTED
    )
    assert isinstance(raw, Ok) and isinstance(adj, Ok)
    assert adj.value.price_basis is PriceBasis.ADJUSTED
    assert raw.value.frame["close"].iloc[-1] != adj.value.frame["close"].iloc[-1]


async def test_empty_result_is_unavailable_not_failure() -> None:
    provider = YahooFinanceProvider(
        downloader=_downloader_returning(pd.DataFrame()), clock=lambda: NOW
    )
    result = await provider.get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert isinstance(result, Unavailable)
    assert "0 bar" in result.reason


async def test_exceptions_map_to_failed_with_retryability() -> None:
    def boom_conn(*a, **k):
        raise ConnectionError("reset by peer")

    def boom_value(*a, **k):
        raise ValueError("bad ticker")

    conn = await YahooFinanceProvider(downloader=boom_conn, clock=lambda: NOW).get_ohlcv(
        "BBCA", Timeframe.D1, None, None
    )
    val = await YahooFinanceProvider(downloader=boom_value, clock=lambda: NOW).get_ohlcv(
        "BBCA", Timeframe.D1, None, None
    )
    assert isinstance(conn, Failed) and conn.retryable is True
    assert isinstance(val, Failed) and val.retryable is False


async def test_invalid_symbol_is_failed_without_network() -> None:
    def never(*a, **k):
        raise AssertionError("downloader tidak boleh dipanggil")

    result = await YahooFinanceProvider(downloader=never).get_ohlcv(
        "BB CA!", Timeframe.D1, None, None
    )
    assert isinstance(result, Failed) and result.retryable is False


async def test_timeout_yields_retryable_failed() -> None:
    def slow(*a, **k):
        time.sleep(0.3)
        return make_yf_frame(5)

    provider = YahooFinanceProvider(downloader=slow, timeout_seconds=0.05, clock=lambda: NOW)
    result = await provider.get_ohlcv("BBCA", Timeframe.D1, None, None)
    assert isinstance(result, Failed) and result.retryable is True and "timeout" in result.error


async def test_semaphore_bounds_concurrency_and_loop_stays_responsive() -> None:
    lock = threading.Lock()
    state = {"active": 0, "max": 0}

    def slow(*a, **k):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.05)
        with lock:
            state["active"] -= 1
        return make_yf_frame(5)

    provider = YahooFinanceProvider(downloader=slow, max_concurrency=2, clock=lambda: NOW)
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.01)

    tick_task = asyncio.create_task(ticker())
    results = await asyncio.gather(
        *(provider.get_ohlcv("BBCA", Timeframe.D1, None, None) for _ in range(6))
    )
    tick_task.cancel()
    assert all(isinstance(r, Ok) for r in results)
    assert state["max"] <= 2
    assert ticks >= 5  # event loop tetap berjalan selama I/O sinkron


async def test_get_quote_from_intraday_and_prev_close(market_rules) -> None:
    intraday = make_yf_frame(30, end_session=date(2026, 3, 16), intraday=True)  # 09:00..09:29 WIB
    daily = make_yf_frame(10, end_session=date(2026, 3, 16))

    def dl(ticker, *, interval, start, end, period):
        return intraday if interval == "1m" else daily

    now = datetime(2026, 3, 16, 2, 31, tzinfo=UTC)  # 09:31 WIB
    provider = YahooFinanceProvider(rules=market_rules, downloader=dl, clock=lambda: now)
    result = await provider.get_quote("BBCA")
    assert isinstance(result, Ok)
    q = result.value
    assert q.symbol == "BBCA" and q.provider == "yahoo"
    assert q.market_time == intraday.index[-1].tz_convert("UTC").to_pydatetime()
    assert float(q.price) == float(intraday["Close"].iloc[-1])
    # prev_close = close bar harian lengkap terakhir (Jumat 13 Mar), bukan bar hari ini yang belum lengkap
    assert float(q.prev_close) == float(daily["Close"].iloc[-2])
    assert q.volume == int(intraday["Volume"].sum())


async def test_health_check_false_on_error() -> None:
    def boom(*a, **k):
        raise ConnectionError("down")

    assert await YahooFinanceProvider(downloader=boom).health_check() is False
    assert (
        await YahooFinanceProvider(
            downloader=_downloader_returning(make_yf_frame(5))
        ).health_check()
        is True
    )
