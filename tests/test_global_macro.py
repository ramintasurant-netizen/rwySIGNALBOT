from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from data.providers.global_macro import (
    GlobalMacroConfig,
    GlobalMacroProvider,
    load_global_macro_config,
)

NOW = datetime(2026, 3, 16, 1, 0, tzinfo=UTC)


def _config() -> GlobalMacroConfig:
    return load_global_macro_config(Path("config/global_macro.yaml"))


def test_repo_macro_config_marks_unsourced_commodities() -> None:
    cfg = _config()
    by_id = {i.id: i for i in cfg.instruments}
    assert cfg.meta.verified is False
    assert all(i.verified is False for i in cfg.instruments)
    for cid in ("coal", "nickel", "cpo"):
        assert by_id[cid].configured is False and "proxy" in by_id[cid].notes
    assert by_id["sp500"].symbol == "^GSPC"


def test_symbol_without_source_rejected() -> None:
    with pytest.raises(ValueError, match="symbol dan source"):
        GlobalMacroConfig.model_validate(
            {"meta": {}, "instruments": [{"id": "x", "label": "X", "symbol": "^X"}]}
        )
    with pytest.raises(ValueError, match="duplikat"):
        GlobalMacroConfig.model_validate(
            {"meta": {}, "instruments": [{"id": "x", "label": "X"}, {"id": "x", "label": "Y"}]}
        )


async def test_snapshot_reports_ok_unavailable_and_failed_separately() -> None:
    def downloader(symbol, *, interval, start, end, period):
        if symbol == "^GSPC":
            idx = pd.date_range("2026-03-11", periods=3, freq="B", tz="America/New_York")
            return pd.DataFrame({"Close": [5000.0, 5050.0, 5100.0]}, index=idx)
        if symbol == "EIDO":
            return pd.DataFrame()
        raise ConnectionError("api_key=secretsecret123 rejected")

    provider = GlobalMacroProvider(_config(), downloader=downloader, clock=lambda: NOW)
    ctx = await provider.snapshot()
    by_id = {i.id: i for i in ctx.items}
    sp = by_id["sp500"]
    assert sp.status == "ok" and sp.last == Decimal("5100.0") and sp.change_pct == Decimal("0.99")
    assert sp.as_of is not None and sp.as_of.tzinfo is not None
    assert by_id["eido"].status == "unavailable"
    assert by_id["gold"].status == "failed" and "secretsecret123" not in by_id["gold"].reason
    assert by_id["coal"].status == "unavailable" and "proxy" in by_id["coal"].reason
    assert {i.id for i in ctx.available} == {"sp500"}
    assert ctx.all_unavailable is False
