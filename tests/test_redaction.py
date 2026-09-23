from __future__ import annotations

from loguru import logger

from core import redaction
from core.logging import configure_logging
from core.redaction import REDACTED, redact, redact_exception

FAKE_TOKEN = "1234567890:AAFakeTokenValueForTests_abcdefghijklmnop"  # bukan token nyata


def test_redact_telegram_token_in_url() -> None:
    text = f"GET https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage failed"
    out = redact(text)
    assert FAKE_TOKEN not in out
    assert REDACTED in out


def test_redact_registered_secret_and_key_params() -> None:
    redaction.registry.clear()
    try:
        redaction.registry.register("sk-supersecretvalue123")
        out = redact(
            "kunci sk-supersecretvalue123 dan api_key=abcdefgh12345678 Bearer abcdefgh.ijkl"
        )
        assert "sk-supersecretvalue123" not in out
        assert "abcdefgh12345678" not in out
        assert "Bearer <REDACTED>" in out
    finally:
        redaction.registry.clear()


def test_redact_exception_message() -> None:
    exc = RuntimeError(f"url https://api.telegram.org/bot{FAKE_TOKEN}/getMe")
    assert FAKE_TOKEN not in redact_exception(exc)


def test_logging_patcher_redacts_message_and_traceback() -> None:
    captured: list[str] = []
    configure_logging(level="DEBUG")
    logger.add(lambda m: captured.append(str(m)), level="DEBUG")
    try:
        logger.info("token bocor {}", FAKE_TOKEN)
        try:
            raise ValueError(f"gagal dengan {FAKE_TOKEN}")
        except ValueError:
            logger.exception("exception dengan token")
    finally:
        logger.remove()
    joined = "\n".join(captured)
    assert FAKE_TOKEN not in joined
    assert REDACTED in joined
    assert "ValueError" in joined
