"""Notifier Telegram: hanya grup/supergroup/channel dalam allowlist, terverifikasi via API.

Pemetaan hasil ``send_message``:
- sukses (ada ``message_id``) → SENT
- ``BadRequest``/``Forbidden``/``ChatMigrated``/``InvalidToken`` → FAILED (pesan tidak terkirim)
- ``RetryAfter`` → FAILED retryable (ditolak sebelum dikirim)
- ``TimedOut``/``NetworkError``/error lain setelah request → UNKNOWN (tidak dikirim ulang otomatis)
Token tidak pernah muncul di error: semua pesan diredaksi.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

from loguru import logger
from telegram import LinkPreviewOptions, constants
from telegram.error import (
    BadRequest,
    ChatMigrated,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)

from config.settings import ChatTarget
from core.redaction import redact, redact_exception
from notifications.base import (
    DeliveryResult,
    DeliveryStatus,
    Notifier,
    TargetRejectedError,
    TargetVerification,
)

ALLOWED_CHAT_TYPES = frozenset(
    {constants.ChatType.GROUP, constants.ChatType.SUPERGROUP, constants.ChatType.CHANNEL}
)
MEMBER_STATUSES = frozenset(
    {
        constants.ChatMemberStatus.MEMBER,
        constants.ChatMemberStatus.ADMINISTRATOR,
        constants.ChatMemberStatus.OWNER,
        constants.ChatMemberStatus.RESTRICTED,
    }
)
MAX_TEXT_LENGTH = int(constants.MessageLimit.MAX_TEXT_LENGTH)


class BotClient(Protocol):
    """Subset ``telegram.Bot`` yang dipakai (memudahkan mock tanpa jaringan)."""

    async def get_me(self) -> Any: ...
    async def get_chat(self, chat_id: int) -> Any: ...
    async def get_chat_member(self, chat_id: int, user_id: int) -> Any: ...
    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> Any: ...


class TelegramNotifier(Notifier):
    def __init__(
        self,
        bot: BotClient,
        *,
        allowed_targets: tuple[ChatTarget, ...],
        verification_ttl_seconds: float = 3600.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot = bot
        self._allowed = {t.chat_id: t for t in allowed_targets}
        self._ttl = verification_ttl_seconds
        self._monotonic = monotonic
        self._cache: dict[int, tuple[float, TargetVerification]] = {}
        self._bot_id: int | None = None

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        return frozenset(self._allowed)

    async def _me(self) -> int:
        if self._bot_id is None:
            me = await self._bot.get_me()
            self._bot_id = int(me.id)
        return self._bot_id

    async def verify_target(self, target: ChatTarget, *, force: bool = False) -> TargetVerification:
        if target.chat_id not in self._allowed:
            return TargetVerification(target, False, reason="chat_id tidak ada dalam allowlist")
        cached = self._cache.get(target.chat_id)
        if cached and not force and self._monotonic() < cached[0] + self._ttl:
            return cached[1]
        try:
            chat = await self._bot.get_chat(target.chat_id)
        except TelegramError as exc:
            return TargetVerification(
                target, False, reason=f"getChat gagal: {redact_exception(exc)}"
            )
        chat_type = str(getattr(chat, "type", ""))
        title = getattr(chat, "title", None)
        if chat_type not in ALLOWED_CHAT_TYPES:
            result = TargetVerification(
                target,
                False,
                chat_type=chat_type,
                title=title,
                reason=f"tipe chat {chat_type!r} ditolak: hanya group/supergroup/channel",
            )
            self._cache[target.chat_id] = (self._monotonic(), result)
            return result
        try:
            member = await self._bot.get_chat_member(target.chat_id, await self._me())
        except TelegramError as exc:
            return TargetVerification(
                target,
                False,
                chat_type=chat_type,
                title=title,
                reason=f"getChatMember gagal: {redact_exception(exc)}",
            )
        status = str(getattr(member, "status", ""))
        reason = ""
        ok = status in MEMBER_STATUSES
        if not ok:
            reason = f"bot tidak menjadi anggota (status {status!r})"
        elif chat_type == constants.ChatType.CHANNEL:
            can_post = bool(getattr(member, "can_post_messages", False))
            if status != constants.ChatMemberStatus.ADMINISTRATOR or not can_post:
                ok, reason = False, "channel: bot harus administrator dengan hak posting"
        elif status == constants.ChatMemberStatus.RESTRICTED and not getattr(
            member, "can_send_messages", True
        ):
            ok, reason = False, "bot dibatasi: tidak boleh mengirim pesan"
        if ok and target.thread_id is not None and not bool(getattr(chat, "is_forum", False)):
            ok, reason = False, "message_thread_id diberikan tetapi chat bukan forum"
        result = TargetVerification(
            target, ok, chat_type=chat_type, title=title, bot_status=status, reason=reason
        )
        self._cache[target.chat_id] = (self._monotonic(), result)
        return result

    async def send(self, target: ChatTarget, text: str) -> DeliveryResult:
        if len(text) > MAX_TEXT_LENGTH:
            return DeliveryResult(
                DeliveryStatus.FAILED, error=f"teks {len(text)} > {MAX_TEXT_LENGTH} karakter"
            )
        verification = await self.verify_target(target)
        if not verification.ok:
            raise TargetRejectedError(verification.reason)
        kwargs: dict[str, Any] = {
            "parse_mode": constants.ParseMode.HTML,
            "link_preview_options": LinkPreviewOptions(is_disabled=True),
        }
        if target.thread_id is not None:
            kwargs["message_thread_id"] = target.thread_id
        try:
            message = await self._bot.send_message(target.chat_id, text, **kwargs)
        except RetryAfter as exc:
            return DeliveryResult(
                DeliveryStatus.FAILED,
                error=f"rate limit Telegram: {redact(str(exc))}",
                retryable=True,
            )
        except (BadRequest, Forbidden, ChatMigrated, InvalidToken) as exc:
            return DeliveryResult(DeliveryStatus.FAILED, error=redact_exception(exc))
        except (TimedOut, NetworkError) as exc:
            logger.warning("pengiriman ke {} ambigu: {}", target, redact_exception(exc))
            return DeliveryResult(DeliveryStatus.UNKNOWN, error=redact_exception(exc))
        except TelegramError as exc:
            return DeliveryResult(DeliveryStatus.UNKNOWN, error=redact_exception(exc))
        message_id = getattr(message, "message_id", None)
        if message_id is None:
            return DeliveryResult(DeliveryStatus.UNKNOWN, error="respons tanpa message_id")
        return DeliveryResult(DeliveryStatus.SENT, message_id=int(message_id))
