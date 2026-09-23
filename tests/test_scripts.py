from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

from telegram.error import BadRequest, TimedOut

from scripts import reconcile_deliveries, test_telegram

FAKE_TOKEN = "1234567890:AAFakeTokenValueForTests_abcdefghijklmnop"


@dataclass
class FakeBot:
    chat_type: str = "supergroup"
    status: str = "administrator"
    send_error: Exception | None = None
    sent: list[str] = field(default_factory=list)

    async def get_me(self):
        return SimpleNamespace(id=1, username="uji_bot")

    async def get_chat(self, chat_id):
        return SimpleNamespace(type=self.chat_type, title="Grup", is_forum=False)

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status=self.status)

    async def send_message(self, chat_id, text, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sent.append(text)
        return SimpleNamespace(message_id=77)


def _settings(settings_factory, **kw):
    base = dict(telegram_bot_token=FAKE_TOKEN, telegram_signal_chat_ids="-1001")
    base.update(kw)
    return settings_factory(**base)


async def test_dry_run_default_sends_nothing(settings_factory, capsys) -> None:
    bot = FakeBot()
    code = await test_telegram.run(_settings(settings_factory), send=False, chat_id=None, bot=bot)
    out = capsys.readouterr().out
    assert code == test_telegram.EXIT_OK and bot.sent == []
    assert "Dry-run selesai" in out and FAKE_TOKEN not in out


async def test_send_requires_explicit_flag_and_confirms_only_on_message_id(
    settings_factory, capsys
) -> None:
    bot = FakeBot()
    code = await test_telegram.run(_settings(settings_factory), send=True, chat_id=None, bot=bot)
    out = capsys.readouterr().out
    assert code == test_telegram.EXIT_OK and len(bot.sent) == 1
    assert "BUKAN SINYAL TRADING" in bot.sent[0] and "entry" not in bot.sent[0].lower()
    assert "message_id=77" in out and "Bukan bukti anggota sudah membaca" in out


async def test_multiple_targets_require_explicit_chat_id(settings_factory, capsys) -> None:
    s = _settings(settings_factory, telegram_signal_chat_ids="-1001,-1002")
    code = await test_telegram.run(s, send=True, chat_id=None, bot=FakeBot())
    assert code == test_telegram.EXIT_REJECTED and "--chat-id" in capsys.readouterr().out
    code = await test_telegram.run(s, send=True, chat_id=-1003, bot=FakeBot())
    assert code == test_telegram.EXIT_REJECTED
    code = await test_telegram.run(s, send=False, chat_id=-1002, bot=FakeBot())
    assert code == test_telegram.EXIT_OK


async def test_private_target_rejected_before_send(settings_factory, capsys) -> None:
    bot = FakeBot(chat_type="private")
    code = await test_telegram.run(_settings(settings_factory), send=True, chat_id=None, bot=bot)
    assert code == test_telegram.EXIT_REJECTED and bot.sent == []
    assert "DITOLAK" in capsys.readouterr().out


async def test_failed_and_ambiguous_outcomes_are_distinguished(settings_factory, capsys) -> None:
    failed = FakeBot(send_error=BadRequest("chat not found"))
    assert (
        await test_telegram.run(_settings(settings_factory), send=True, chat_id=None, bot=failed)
        == test_telegram.EXIT_FAILED
    )
    assert "GAGAL sebelum terkirim" in capsys.readouterr().out
    ambiguous = FakeBot(send_error=TimedOut())
    assert (
        await test_telegram.run(_settings(settings_factory), send=True, chat_id=None, bot=ambiguous)
        == test_telegram.EXIT_AMBIGUOUS
    )
    out = capsys.readouterr().out
    assert "AMBIGU" in out and "TIDAK dikirim ulang" in out


async def test_missing_token_rejected(settings_factory, capsys) -> None:
    code = await test_telegram.run(
        settings_factory(telegram_signal_chat_ids="-1001"), send=True, chat_id=None, bot=FakeBot()
    )
    assert (
        code == test_telegram.EXIT_REJECTED
        and "TELEGRAM_BOT_TOKEN kosong" in capsys.readouterr().out
    )


def test_reconcile_cli_on_empty_db(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    assert reconcile_deliveries.main(["--env-file", "-", "list"]) == 0
    assert "Tidak ada pengiriman" in capsys.readouterr().out
    assert reconcile_deliveries.main(["--env-file", "-", "mark-sent", "99", "--by", "admin:1"]) == 1


async def test_discover_lists_only_group_chats(settings_factory, capsys) -> None:
    class DiscoverBot(FakeBot):
        async def get_updates(self, timeout=0):
            return [
                SimpleNamespace(
                    effective_chat=SimpleNamespace(id=-1001, type="supergroup", title="Grup A")
                ),
                SimpleNamespace(effective_chat=SimpleNamespace(id=555, type="private", title=None)),
                SimpleNamespace(
                    effective_chat=SimpleNamespace(id=-1002, type="channel", title="Kanal")
                ),
            ]

    code = await test_telegram.discover(_settings(settings_factory), bot=DiscoverBot())
    out = capsys.readouterr().out
    assert code == test_telegram.EXIT_OK
    assert "-1001" in out and "-1002" in out and "555" not in out
