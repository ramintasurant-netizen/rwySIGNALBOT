from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from config.common import UNVERIFIED_LABEL, ConfigError
from config.market_rules import MarketRules
from core.timeutil import WIB


def test_repo_rules_are_labeled_unverified(market_rules: MarketRules) -> None:
    assert market_rules.verified is False
    assert market_rules.label == UNVERIFIED_LABEL
    assert market_rules.lot_size == 100


@pytest.mark.parametrize(
    ("price", "tick"),
    [
        ("1", "1"),
        ("199", "1"),
        ("200", "2"),
        ("499", "2"),
        ("500", "5"),
        ("1999", "5"),
        ("2000", "10"),
        ("4999", "10"),
        ("5000", "25"),
        ("125000", "25"),
    ],
)
def test_tick_lookup_at_band_boundaries(market_rules: MarketRules, price: str, tick: str) -> None:
    assert market_rules.tick_for(Decimal(price)) == Decimal(tick)


def test_price_limit_band_lookup(market_rules: MarketRules) -> None:
    assert market_rules.price_limit_band_for(Decimal("150")).up_pct == Decimal("35")
    assert market_rules.price_limit_band_for(Decimal("200")).up_pct == Decimal("25")
    assert market_rules.price_limit_band_for(Decimal("5000")).up_pct == Decimal("20")


def test_friday_schedule_differs(market_rules: MarketRules) -> None:
    regular = market_rules.schedule_for(date(2026, 3, 12))  # Kamis
    friday = market_rules.schedule_for(date(2026, 3, 13))
    assert regular.session_1.end == time(12, 0)
    assert friday.session_1.end == time(11, 30)
    assert friday.session_2.start == time(14, 0)


def test_daily_bar_completion(market_rules: MarketRules) -> None:
    session = date(2026, 3, 12)
    before_close = datetime(2026, 3, 12, 15, 0, tzinfo=WIB)
    just_after_post = datetime(2026, 3, 12, 16, 20, tzinfo=WIB)
    after_buffer = datetime(2026, 3, 12, 16, 31, tzinfo=WIB)
    assert market_rules.daily_bar_final_time(session) == datetime(2026, 3, 12, 16, 30, tzinfo=WIB)
    assert market_rules.is_daily_bar_complete(session, before_close) is False
    assert market_rules.is_daily_bar_complete(session, just_after_post) is False
    assert market_rules.is_daily_bar_complete(session, after_buffer) is True
    assert (
        market_rules.is_daily_bar_complete(session, datetime(2026, 3, 13, 1, 0, tzinfo=UTC)) is True
    )
    assert market_rules.is_daily_bar_complete(date(2026, 3, 13), before_close) is False


def _base_rules() -> dict:
    return {
        "meta": {"verified": False},
        "lot_size": 100,
        "tick_table": [
            {"min_price": 0, "max_price": 200, "tick": 1},
            {"min_price": 200, "max_price": None, "tick": 2},
        ],
        "price_limits": {
            "min_price": 50,
            "bands": [{"min_price": 0, "max_price": None, "up_pct": 25, "down_pct": 15}],
        },
        "sessions": {
            "regular": {
                "pre_opening": {"start": "08:45", "end": "08:59"},
                "session_1": {"start": "09:00", "end": "12:00"},
                "session_2": {"start": "13:30", "end": "15:49"},
                "pre_closing": {"start": "15:50", "end": "16:00"},
            },
            "friday": {
                "pre_opening": {"start": "08:45", "end": "08:59"},
                "session_1": {"start": "09:00", "end": "11:30"},
                "session_2": {"start": "14:00", "end": "15:49"},
                "pre_closing": {"start": "15:50", "end": "16:00"},
            },
        },
        "liquidity": {
            "min_avg_daily_value_idr": 0,
            "min_avg_daily_volume_shares": 0,
            "lookback_days": 20,
        },
    }


def test_tick_table_gap_rejected() -> None:
    data = _base_rules()
    data["tick_table"] = [
        {"min_price": 0, "max_price": 200, "tick": 1},
        {"min_price": 250, "max_price": None, "tick": 2},
    ]
    with pytest.raises(ValueError, match="harus sama dengan max_price"):
        MarketRules.model_validate(data)


def test_last_band_must_be_open_ended() -> None:
    data = _base_rules()
    data["tick_table"] = [{"min_price": 0, "max_price": 200, "tick": 1}]
    with pytest.raises(ValueError, match="terbuka"):
        MarketRules.model_validate(data)


def test_verified_requires_source_and_effective_date() -> None:
    data = _base_rules()
    data["meta"] = {"verified": True}
    with pytest.raises(ValueError, match="meta.source"):
        MarketRules.model_validate(data)
    data["meta"] = {
        "verified": True,
        "source": "https://idx.example/peraturan",
        "effective_from": "2026-01-01",
    }
    with pytest.raises(ValueError, match="label"):
        MarketRules.model_validate(data)
    data["meta"]["label"] = "Peraturan II-A (contoh test)"
    rules = MarketRules.model_validate(data)
    assert rules.verified is True and rules.label == "Peraturan II-A (contoh test)"


def test_overlapping_sessions_rejected() -> None:
    data = _base_rules()
    data["sessions"]["regular"]["session_2"] = {"start": "11:00", "end": "15:49"}
    with pytest.raises(ValueError, match="berurutan"):
        MarketRules.model_validate(data)


def test_missing_file_raises_config_error(tmp_path) -> None:
    from config.market_rules import load_market_rules

    with pytest.raises(ConfigError):
        load_market_rules(tmp_path / "nope.yaml")
