from __future__ import annotations

from datetime import date

from bot.gates import evaluate_gates
from config.settings import ChatTarget
from notifications.base import TargetVerification

FAKE_TOKEN = "1234567890:AAFakeTokenValueForTests_abcdefghijklmnop"
TARGET = ChatTarget(chat_id=-1001)
VERSIONS = {"trend_pullback": "1.0.0", "breakout": "1.0.0"}


def _gates(settings, market_rules, calendar, **kw):
    base = dict(
        paused=False,
        backtest_gate=None,
        strategy_versions=VERSIONS,
        config_hash="abc",
        target_verifications=None,
    )
    base.update(kw)
    return evaluate_gates(settings, market_rules, calendar, date(2026, 3, 16), **base)


def test_development_dry_run_warns_but_allows_job(settings_factory, market_rules, calendar) -> None:
    g = _gates(settings_factory(), market_rules, calendar)
    assert g.job_allowed and not g.publish_allowed
    assert any("belum terverifikasi" in w for w in g.warnings)
    assert any("gate backtest" in w for w in g.warnings)
    assert any("pengiriman live nonaktif" in b for b in g.publish_blockers)


def test_weekend_holiday_and_pause_are_hard_blockers(
    settings_factory, market_rules, calendar
) -> None:
    s = settings_factory()
    weekend = evaluate_gates(
        s,
        market_rules,
        calendar,
        date(2026, 3, 14),
        paused=False,
        backtest_gate=None,
        strategy_versions=VERSIONS,
        config_hash="a",
        target_verifications=None,
    )
    assert not weekend.job_allowed and "bukan hari perdagangan" in weekend.hard_blockers[0]
    out = evaluate_gates(
        s,
        market_rules,
        calendar,
        date(2027, 6, 1),
        paused=False,
        backtest_gate=None,
        strategy_versions=VERSIONS,
        config_hash="a",
        target_verifications=None,
    )
    assert not out.job_allowed and "cakupan" in out.hard_blockers[0]
    paused = _gates(s, market_rules, calendar, paused=True)
    assert not paused.job_allowed and "pause" in paused.hard_blockers[0]


def test_production_blocks_publish_until_everything_verified(
    settings_factory, market_rules, calendar
) -> None:
    s = settings_factory(
        app_env="production",
        app_mode="live",
        database_url="postgresql+asyncpg://u:p@h/db",
        telegram_bot_token=FAKE_TOKEN,
        telegram_enable_live_send=True,
        telegram_signal_chat_ids="-1001",
    )
    g = _gates(
        s, market_rules, calendar, target_verifications={-1001: TargetVerification(TARGET, True)}
    )
    assert g.job_allowed and not g.publish_allowed
    joined = " | ".join(g.publish_blockers)
    assert "market_rules.yaml belum terverifikasi" in joined
    assert "trading_calendar.yaml belum terverifikasi" in joined
    assert "gate backtest belum tersedia" in joined
    assert "≥2 provider" in joined
    assert g.warnings == ()


def test_target_verification_required_for_live(settings_factory, market_rules, calendar) -> None:
    s = settings_factory(
        app_mode="live",
        telegram_bot_token=FAKE_TOKEN,
        telegram_enable_live_send=True,
        telegram_signal_chat_ids="-1001",
    )
    missing = _gates(s, market_rules, calendar, target_verifications={})
    assert any("belum diverifikasi via API" in b for b in missing.publish_blockers)
    rejected = _gates(
        s,
        market_rules,
        calendar,
        target_verifications={-1001: TargetVerification(TARGET, False, reason="private")},
    )
    assert any("ditolak: private" in b for b in rejected.publish_blockers)
    ok = _gates(
        s, market_rules, calendar, target_verifications={-1001: TargetVerification(TARGET, True)}
    )
    assert ok.publish_allowed  # development: aturan belum terverifikasi hanya warning


def test_backtest_gate_binding(settings_factory, market_rules, calendar) -> None:
    s = settings_factory()
    passed = {"passed": True, "config_hash": "abc", "strategy_versions": VERSIONS}
    assert not any(
        "gate backtest" in w
        for w in _gates(s, market_rules, calendar, backtest_gate=passed).warnings
    )
    other_hash = _gates(s, market_rules, calendar, backtest_gate={**passed, "config_hash": "zzz"})
    assert any("config_hash berbeda" in w for w in other_hash.warnings)
    failed = _gates(s, market_rules, calendar, backtest_gate={**passed, "passed": False})
    assert any("TIDAK lulus" in w for w in failed.warnings)
    partial = _gates(
        s,
        market_rules,
        calendar,
        backtest_gate={**passed, "strategy_versions": {"breakout": "1.0.0"}},
    )
    assert any("tidak mencakup trend_pullback" in w for w in partial.warnings)
