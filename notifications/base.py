from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from config.settings import ChatTarget


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENDING = "sending"
    SENT = "sent"  # hanya setelah API mengonfirmasi (message_id)
    FAILED = "failed"  # ditolak/gagal SEBELUM pesan mungkin terkirim
    UNKNOWN = "unknown"  # request terkirim, respons hilang: JANGAN kirim ulang otomatis


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    status: DeliveryStatus
    message_id: int | None = None
    error: str = ""
    retryable: bool = False  # hanya bermakna untuk FAILED (mis. RetryAfter sebelum terkirim)

    @property
    def sent(self) -> bool:
        return self.status is DeliveryStatus.SENT


@dataclass(frozen=True, slots=True)
class TargetVerification:
    target: ChatTarget
    ok: bool
    chat_type: str | None = None
    title: str | None = None
    bot_status: str | None = None
    reason: str = ""


class TargetRejectedError(PermissionError):
    """Tujuan bukan grup/supergroup/channel yang diizinkan."""


class Notifier(ABC):
    @abstractmethod
    async def verify_target(
        self, target: ChatTarget, *, force: bool = False
    ) -> TargetVerification: ...

    @abstractmethod
    async def send(self, target: ChatTarget, text: str) -> DeliveryResult: ...
