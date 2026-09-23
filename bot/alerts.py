"""Alert operasional ke grup admin dengan pembatasan frekuensi; tanpa admin chat → log saja.
Tidak pernah fallback ke chat pribadi."""

from __future__ import annotations

import time
from collections.abc import Callable

from loguru import logger

from bot.formatter import esc
from config.settings import ChatTarget
from core.redaction import redact
from notifications.base import Notifier, TargetRejectedError


class AlertSink:
    def __init__(
        self,
        notifier: Notifier | None,
        admin_target: ChatTarget | None,
        *,
        min_interval_seconds: float = 1800.0,
        monotonic: Callable[[], float] = time.monotonic,
        enabled: bool = True,
    ) -> None:
        self._notifier = notifier
        self._target = admin_target
        self._interval = min_interval_seconds
        self._monotonic = monotonic
        self._enabled = enabled
        self._last: dict[str, float] = {}
        self.history: list[tuple[str, str, str]] = []  # (kind, message, outcome)

    async def alert(self, kind: str, message: str, *, force: bool = False) -> str:
        """Kembalikan outcome: sent | logged | suppressed | failed | unknown."""
        message = redact(message)
        now = self._monotonic()
        last = self._last.get(kind)
        if not force and last is not None and now - last < self._interval:
            self.history.append((kind, message, "suppressed"))
            return "suppressed"
        self._last[kind] = now
        logger.warning("ALERT [{}]: {}", kind, message)
        if self._notifier is None or self._target is None or not self._enabled:
            self.history.append((kind, message, "logged"))
            return "logged"
        text = f"<b>⚠️ ALERT [{esc(kind)}]</b>\n{esc(message)}"
        try:
            result = await self._notifier.send(self._target, text[:4000])
        except TargetRejectedError as exc:
            logger.error("alert admin ditolak: {}", exc)
            self.history.append((kind, message, "failed"))
            return "failed"
        outcome = result.status.value if result.status.value in ("sent", "unknown") else "failed"
        self.history.append((kind, message, outcome))
        return outcome
