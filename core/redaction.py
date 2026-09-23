"""Redaksi rahasia dari teks bebas (log, pesan exception, URL)."""

from __future__ import annotations

import re
from collections.abc import Iterable

REDACTED = "<REDACTED>"

# Token bot Telegram: "<digit>:<35+ karakter>"; muncul juga di URL api.telegram.org/bot<token>/...
# Tanpa \b di depan: pada "bot123456:..." tidak ada word boundary antara "t" dan digit.
_TELEGRAM_TOKEN_RE = re.compile(r"(?<!\d)\d{6,}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")
# Nilai parameter query/header yang lazim memuat kunci API.
_KEY_PARAM_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|access[_-]?token|authorization|secret)"
    r"(\s*[=:]\s*)(\"?)([A-Za-z0-9_\-\.]{8,})(\"?)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.=]{8,}")


class SecretRegistry:
    """Kumpulan nilai rahasia yang harus dihapus dari teks apa pun."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def register(self, *values: str | None) -> None:
        for value in values:
            if value and len(value) >= 6:
                self._secrets.add(value)

    def clear(self) -> None:
        self._secrets.clear()

    def values(self) -> frozenset[str]:
        return frozenset(self._secrets)


registry = SecretRegistry()


def redact(text: str, extra_secrets: Iterable[str] = ()) -> str:
    if not text:
        return text
    out = text
    secrets = sorted({*registry.values(), *(s for s in extra_secrets if s)}, key=len, reverse=True)
    for secret in secrets:
        out = out.replace(secret, REDACTED)
    out = _TELEGRAM_TOKEN_RE.sub(REDACTED, out)
    out = _BEARER_RE.sub(f"Bearer {REDACTED}", out)
    out = _KEY_PARAM_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{REDACTED}{m.group(5)}", out
    )
    return out


def redact_exception(exc: BaseException) -> str:
    return redact(f"{type(exc).__name__}: {exc}")
