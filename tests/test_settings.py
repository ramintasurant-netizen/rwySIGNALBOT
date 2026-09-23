from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import ChatTarget, parse_chat_targets
from core import redaction
from core.redaction import redact

FAKE_TOKEN = "1234567890:AAFakeTokenValueForTests_abcdefghijklmnop"


def test_defaults_are_safe(settings_factory) -> None:
    s = settings_factory()
    assert s.app_env == "development"
    assert s.app_mode == "dry_run"
    assert s.telegram_enable_live_send is False
    assert s.llm_provider == "none"
    assert s.whatsapp_export_enabled is False
    assert s.goapi_enabled is False and s.sectors_enabled is False and s.broker_x_enabled is False
    assert s.live_send_allowed is False
    assert s.provider_order == ("yahoo",)
    assert s.signal_targets == ()
    assert any("TELEGRAM_ADMIN_CHAT_ID kosong" in w for w in s.config_warnings)


def test_live_mode_requires_token_targets_and_switch(settings_factory) -> None:
    with pytest.raises(ValidationError) as exc:
        settings_factory(app_mode="live")
    msg = str(exc.value)
    assert "TELEGRAM_BOT_TOKEN kosong" in msg
    assert "TELEGRAM_SIGNAL_CHAT_IDS kosong" in msg
    assert "TELEGRAM_ENABLE_LIVE_SEND=false" in msg


def test_live_mode_complete_configuration(settings_factory) -> None:
    s = settings_factory(
        app_mode="live",
        telegram_bot_token=FAKE_TOKEN,
        telegram_enable_live_send=True,
        telegram_signal_chat_ids="-1001234567890:7,-1009876543210",
        telegram_admin_chat_id="-1001111111111",
        telegram_admin_user_ids="42, 43",
    )
    assert s.live_send_allowed is True
    assert s.signal_targets == (
        ChatTarget(chat_id=-1001234567890, thread_id=7),
        ChatTarget(chat_id=-1009876543210, thread_id=None),
    )
    assert s.admin_target == ChatTarget(chat_id=-1001111111111)
    assert s.admin_user_ids == (42, 43)
    assert s.allowed_chat_ids == frozenset({-1001234567890, -1009876543210, -1001111111111})


@pytest.mark.parametrize("raw", ["123456789", "0", "abc", "-100123:x", "-100123:"])
def test_chat_target_rejects_private_or_malformed(raw: str) -> None:
    with pytest.raises(ValueError):
        ChatTarget.parse(raw)


def test_parse_chat_targets_rejects_duplicates() -> None:
    with pytest.raises(ValueError, match="duplikat"):
        parse_chat_targets("-1001,-1001")


def test_admin_chat_positive_id_rejected(settings_factory) -> None:
    with pytest.raises(ValidationError, match="chat pribadi"):
        settings_factory(telegram_admin_chat_id="123456")


def test_admin_user_ids_must_be_positive(settings_factory) -> None:
    with pytest.raises(ValidationError, match="user_id harus positif"):
        settings_factory(telegram_admin_user_ids="-5")


def test_production_requires_postgres_unless_explicit(settings_factory) -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        settings_factory(app_env="production")
    s = settings_factory(app_env="production", allow_sqlite_in_production=True)
    assert any("SQLite" in w for w in s.config_warnings)
    s2 = settings_factory(app_env="production", database_url="postgresql+asyncpg://u:p@db/x")
    assert s2.requires_cross_validation is True
    s3 = settings_factory(
        app_env="production",
        database_url="postgresql+asyncpg://u:p@db/x",
        production_single_provider_approved=True,
    )
    assert s3.requires_cross_validation is False


@pytest.mark.parametrize("flag", ["goapi_enabled", "sectors_enabled", "broker_x_enabled"])
def test_unverified_providers_cannot_be_enabled(settings_factory, flag: str) -> None:
    with pytest.raises(ValidationError, match="belum diverifikasi"):
        settings_factory(**{flag: True})


def test_provider_priority_validation(settings_factory) -> None:
    with pytest.raises(ValidationError, match="tidak dikenal"):
        settings_factory(provider_priority="yahoo,bloomberg")
    with pytest.raises(ValidationError, match="nonaktif"):
        settings_factory(provider_priority="yahoo,goapi")
    with pytest.raises(ValidationError, match="duplikat"):
        settings_factory(provider_priority="yahoo,yahoo")
    with pytest.raises(ValidationError, match="kosong"):
        settings_factory(provider_priority=" , ")


def test_llm_configuration_combinations(settings_factory) -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        settings_factory(llm_provider="openai", llm_model="x")
    with pytest.raises(ValidationError, match="LLM_BASE_URL"):
        settings_factory(llm_provider="openai_compatible", llm_model="x")
    with pytest.raises(ValidationError, match="LLM_MODEL"):
        settings_factory(llm_provider="anthropic", llm_api_key="k" * 20)
    s = settings_factory(
        llm_provider="openai_compatible", llm_base_url="http://localhost:11434", llm_model="m"
    )
    assert s.llm_provider == "openai_compatible"


def test_invalid_token_format_rejected(settings_factory) -> None:
    with pytest.raises(ValidationError, match="format token"):
        settings_factory(telegram_bot_token="not-a-token")


def test_schedule_and_timezone_validation(settings_factory) -> None:
    with pytest.raises(ValidationError):
        settings_factory(schedule_morning="8h30")
    with pytest.raises(ValidationError, match="zona waktu"):
        settings_factory(app_timezone="Mars/Olympus")


def test_secrets_never_appear_in_summary_and_are_redactable(settings_factory) -> None:
    redaction.registry.clear()
    try:
        s = settings_factory(telegram_bot_token=FAKE_TOKEN, llm_api_key="sk-abcdefghijklmnop123456")
        summary = repr(s.sanitized_summary())
        assert FAKE_TOKEN not in summary and "sk-abcdefghijklmnop123456" not in summary
        assert summary.count("True") >= 1  # telegram_token_configured
        s.register_secrets()
        assert "sk-abcdefghijklmnop123456" not in redact("kunci: sk-abcdefghijklmnop123456")
        assert FAKE_TOKEN not in repr(s)
    finally:
        redaction.registry.clear()


def test_admin_chat_equal_signal_chat_warns(settings_factory) -> None:
    s = settings_factory(telegram_signal_chat_ids="-1001", telegram_admin_chat_id="-1001")
    assert any("sama dengan" in w for w in s.config_warnings)


def test_engine_configs_from_settings(settings_factory) -> None:
    from decimal import Decimal

    from engine.risk import RiskConfig
    from engine.scorer import ScorerConfig

    s = settings_factory(risk_min_rr="2.5", sizing_capital_example="50000000", scorer_threshold=80)
    risk = RiskConfig.from_settings(s)
    assert risk.min_rr == Decimal("2.5") and risk.capital_example == Decimal("50000000")
    assert risk.fee_buy_pct == Decimal("0.15") and risk.apply_fees_to_rr is True
    scorer = ScorerConfig.from_settings(s)
    assert scorer.threshold == 80 and scorer.max_signals == 5
