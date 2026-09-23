"""Adapter python-telegram-bot → CommandRouter. Tidak ada logika otorisasi di sini; semua ada di
``bot.commands`` agar dapat diuji tanpa objek PTB. Balasan hanya ke chat asal (yang sudah lolos allowlist)."""

from __future__ import annotations

from loguru import logger
from telegram import LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, filters

from bot.commands import ADMIN_COMMANDS, MEMBER_COMMANDS, CommandRouter, IncomingCommand, Reply
from core.redaction import redact_exception

ROUTER_KEY = "command_router"


def to_incoming(update: Update) -> IncomingCommand | None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.text:
        return None
    text = message.text.strip()
    if not text.startswith("/"):
        return None
    head, *args = text.split()
    command = head[1:].split("@", 1)[0].lower()
    user = message.from_user
    return IncomingCommand(
        chat_id=chat.id,
        chat_type=str(chat.type),
        command=command,
        args=tuple(args),
        user_id=user.id if user else None,
        user_is_bot=bool(user.is_bot) if user else False,
        sender_chat_id=message.sender_chat.id if message.sender_chat else None,
        thread_id=message.message_thread_id
        if getattr(message, "is_topic_message", False)
        else None,
    )


async def deliver_reply(context: ContextTypes.DEFAULT_TYPE, reply: Reply) -> None:
    for part in reply.parts:
        kwargs: dict[str, object] = {
            "parse_mode": ParseMode.HTML,
            "link_preview_options": LinkPreviewOptions(is_disabled=True),
        }
        if reply.thread_id is not None:
            kwargs["message_thread_id"] = reply.thread_id
        await context.bot.send_message(reply.chat_id, part, **kwargs)


async def on_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    router: CommandRouter = context.application.bot_data[ROUTER_KEY]
    incoming = to_incoming(update)
    if incoming is None:
        return
    try:
        reply = await router.handle(incoming)
    except Exception as exc:  # noqa: BLE001 - handler tidak boleh menjatuhkan polling
        logger.exception("handler /{} gagal: {}", incoming.command, redact_exception(exc))
        if router.chat_allowed(incoming):
            reply = Reply(
                incoming.chat_id,
                ("Terjadi kesalahan internal; sudah dicatat.",),
                incoming.thread_id,
            )
        else:
            reply = None
    if reply is not None:
        await deliver_reply(context, reply)


def register_handlers(app: Application, router: CommandRouter) -> None:
    app.bot_data[ROUTER_KEY] = router
    # Hanya grup/supergroup/channel; pesan private tidak pernah memicu handler (tanpa balasan).
    group_filter = filters.ChatType.GROUPS | filters.ChatType.CHANNEL
    app.add_handler(
        CommandHandler(list(MEMBER_COMMANDS + ADMIN_COMMANDS), on_command, filters=group_filter)
    )
