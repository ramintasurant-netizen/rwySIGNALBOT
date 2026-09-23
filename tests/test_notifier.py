from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut

from config.settings import ChatTarget
from notifications.base import DeliveryStatus, TargetRejectedError
from notifications.telegram import TelegramNotifier

FAKE_TOKEN = "1234567890:AAFakeTokenValueForTests_abcdefghijklmnop"


@dataclass
class FakeBot:
    chats: dict[int, SimpleNamespace] = field(default_factory=dict)
    members: dict[int, SimpleNamespace] = field(default_factory=dict)
    send_error: Exception | None = None
    sent: list[tuple[int, str, dict]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    next_message_id: int = 100

    async def get_me(self):
        self.calls.append("get_me")
        return SimpleNamespace(id=999, username="uji_bot")

    async def get_chat(self, chat_id):
        self.calls.append("get_chat")
        if chat_id not in self.chats:
            raise BadRequest("Chat not found")
        return self.chats[chat_id]

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append("get_chat_member")
        return self.members[chat_id]

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append("send_message")
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((chat_id, text, kwargs))
        self.next_message_id += 1
        return SimpleNamespace(message_id=self.next_message_id)


def _bot(chat_type="supergroup", status="administrator", **member_extra) -> FakeBot:
    bot = FakeBot()
    bot.chats[-1001] = SimpleNamespace(type=chat_type, title="Grup Uji", is_forum=False)
    bot.members[-1001] = SimpleNamespace(status=status, **member_extra)
    bot.chats[12345] = SimpleNamespace(type="private", title=None)
    bot.members[12345] = SimpleNamespace(status="member")
    return bot


T = ChatTarget(chat_id=-1001)


async def test_verify_group_ok_and_cached() -> None:
    bot = _bot()
    n = TelegramNotifier(bot, allowed_targets=(T,))
    v1 = await n.verify_target(T)
    v2 = await n.verify_target(T)
    assert v1.ok and v1.chat_type == "supergroup" and v1.title == "Grup Uji"
    assert v2 == v1 and bot.calls.count("get_chat") == 1  # cache
    assert (await n.verify_target(T, force=True)).ok and bot.calls.count("get_chat") == 2


async def test_private_chat_rejected_even_if_in_allowlist() -> None:
    bot = _bot()
    target = ChatTarget.model_construct(
        chat_id=12345, thread_id=None
    )  # lewati validasi format untuk uji jalur API
    n = TelegramNotifier(bot, allowed_targets=(target,))
    ver = await n.verify_target(target)
    assert not ver.ok and "private" in ver.reason
    with pytest.raises(TargetRejectedError):
        await n.send(target, "x")
    assert bot.sent == []


async def test_not_in_allowlist_rejected_without_api_calls() -> None:
    bot = _bot()
    n = TelegramNotifier(bot, allowed_targets=(T,))
    ver = await n.verify_target(ChatTarget(chat_id=-1009))
    assert not ver.ok and "allowlist" in ver.reason and bot.calls == []


async def test_bot_must_be_member_and_channel_needs_post_rights() -> None:
    left = _bot(status="left")
    assert not (await TelegramNotifier(left, allowed_targets=(T,)).verify_target(T)).ok
    ch_member = _bot(chat_type="channel", status="member")
    v = await TelegramNotifier(ch_member, allowed_targets=(T,)).verify_target(T)
    assert not v.ok and "hak posting" in v.reason
    ch_admin_nopost = _bot(chat_type="channel", status="administrator", can_post_messages=False)
    assert not (await TelegramNotifier(ch_admin_nopost, allowed_targets=(T,)).verify_target(T)).ok
    ch_ok = _bot(chat_type="channel", status="administrator", can_post_messages=True)
    assert (await TelegramNotifier(ch_ok, allowed_targets=(T,)).verify_target(T)).ok
    restricted = _bot(status="restricted", can_send_messages=False)
    assert not (await TelegramNotifier(restricted, allowed_targets=(T,)).verify_target(T)).ok


async def test_thread_requires_forum() -> None:
    bot = _bot()
    target = ChatTarget(chat_id=-1001, thread_id=7)
    v = await TelegramNotifier(bot, allowed_targets=(target,)).verify_target(target)
    assert not v.ok and "forum" in v.reason
    bot.chats[-1001].is_forum = True
    n = TelegramNotifier(bot, allowed_targets=(target,))
    assert (await n.verify_target(target)).ok
    result = await n.send(target, "<b>halo</b>")
    assert result.sent and bot.sent[0][2]["message_thread_id"] == 7
    assert bot.sent[0][2]["parse_mode"] == "HTML"


async def test_send_status_mapping() -> None:
    ok = TelegramNotifier(_bot(), allowed_targets=(T,))
    r = await ok.send(T, "x")
    assert r.status is DeliveryStatus.SENT and r.message_id == 101

    for exc, expected, retryable in [
        (BadRequest("can't parse entities"), DeliveryStatus.FAILED, False),
        (Forbidden("bot was kicked"), DeliveryStatus.FAILED, False),
        (RetryAfter(3), DeliveryStatus.FAILED, True),
        (TimedOut(), DeliveryStatus.UNKNOWN, False),
        (NetworkError("connection reset"), DeliveryStatus.UNKNOWN, False),
    ]:
        bot = _bot()
        bot.send_error = exc
        r = await TelegramNotifier(bot, allowed_targets=(T,)).send(T, "x")
        assert r.status is expected, exc
        assert r.retryable is retryable


async def test_error_messages_are_redacted() -> None:
    bot = _bot()
    bot.send_error = BadRequest(
        f"url https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage rejected"
    )
    r = await TelegramNotifier(bot, allowed_targets=(T,)).send(T, "x")
    assert FAKE_TOKEN not in r.error and "REDACTED" in r.error


async def test_oversized_text_rejected_before_api() -> None:
    bot = _bot()
    r = await TelegramNotifier(bot, allowed_targets=(T,)).send(T, "x" * 5000)
    assert r.status is DeliveryStatus.FAILED and "send_message" not in bot.calls
