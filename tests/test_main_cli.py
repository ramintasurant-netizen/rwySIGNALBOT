"""Smoke test CLI: berjalan tanpa token, tanpa jaringan, tanpa .env."""

from __future__ import annotations

import json

import main


def test_config_command_runs_without_token_and_hides_secrets(capsys) -> None:
    code = main.main(["--env-file", "-", "config"])
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out)
    assert payload["settings"]["app_mode"] == "dry_run"
    assert payload["settings"]["telegram_token_configured"] is False
    assert payload["config_verification"]["market_rules"]["verified"] is False
    assert any("belum terverifikasi" in b for b in payload["production_blockers"])
    assert "TELEGRAM_BOT_TOKEN" not in out


def test_dryrun_refuses_live_mode(monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_MODE", "live")
    monkeypatch.setenv("TELEGRAM_ENABLE_LIVE_SEND", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1234567890:AAFakeTokenValueForTests_abcdefghijklmnop")
    monkeypatch.setenv("TELEGRAM_SIGNAL_CHAT_IDS", "-1001")
    assert main.main(["--env-file", "-", "dryrun", "morning"]) == 2
    assert "APP_MODE=dry_run" in capsys.readouterr().err


def test_invalid_env_reports_clear_error(monkeypatch, capsys) -> None:
    monkeypatch.setenv("APP_MODE", "live")
    assert main.main(["--env-file", "-", "config"]) == 2
    assert "APP_MODE=live membutuhkan" in capsys.readouterr().err
