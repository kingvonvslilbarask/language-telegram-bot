"""Telegram-facing command handlers (what people type/tap inside Telegram).

Commands:
  /start [token]  - with a token: link this chat to a website account (deep link
                    https://t.me/<bot>?start=<token>). Without: friendly greeting.
  /help           - what the bot can do
  /id             - shows the chat ID (teachers/admins need it once for setup;
                    works inside a group too, to get the group's chat ID)
  /myclasses      - upcoming classes, read from the web backend
Anything else typed in a private chat gets a short polite reply.
"""
from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from . import messages
from .backend_client import BackendError, InvalidLinkToken, WebBackendClient
from .config import Settings

logger = logging.getLogger(__name__)

# Telegram deep-link payloads are 1-64 chars of A-Z a-z 0-9 _ -
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _backend(context: ContextTypes.DEFAULT_TYPE) -> WebBackendClient:
    return context.application.bot_data["backend"]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, user, message = update.effective_chat, update.effective_user, update.effective_message
    if chat is None or message is None:
        return

    token = context.args[0] if context.args else None
    if token is None:
        await message.reply_text(messages.start_greeting(_settings(context).school_name))
        return

    if not _TOKEN_RE.match(token):
        await message.reply_text(messages.LINK_INVALID)
        return

    backend = _backend(context)
    if not backend.enabled:
        logger.error("/start with a token received but WEB_BACKEND_URL is not configured")
        await message.reply_text(messages.LINK_UNAVAILABLE)
        return

    try:
        result = await backend.link_telegram(
            token=token,
            chat_id=chat.id,
            username=user.username if user else None,
            first_name=user.first_name if user else None,
        )
    except InvalidLinkToken:
        logger.info("Rejected link token from chat %s", chat.id)
        await message.reply_text(messages.LINK_INVALID)
    except BackendError:
        logger.exception("Could not link chat %s", chat.id)
        await message.reply_text(messages.LINK_ERROR)
    else:
        logger.info("Linked chat %s as %s", chat.id, result.role)
        await message.reply_text(messages.linked_ok(result.display_name, result.role))
        # A student usually pays BEFORE connecting Telegram, so the payment-time welcome could not be
        # delivered. Onboard them now: send the "how classes work on Telegram" video right after linking.
        video = context.application.bot_data.get("video")
        if result.role == "student" and video is not None:
            await video.send(context.bot, chat.id, messages.video_caption(_settings(context).school_name))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(messages.HELP_TEXT)


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_message:
        await update.effective_message.reply_text(messages.id_text(update.effective_chat.id))


async def myclasses_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat, message = update.effective_chat, update.effective_message
    if chat is None or message is None:
        return
    backend = _backend(context)
    if not backend.enabled:
        await message.reply_text(messages.CLASSES_UNAVAILABLE)
        return
    try:
        classes = await backend.get_classes(chat.id)
    except BackendError:
        logger.exception("Could not load classes for chat %s", chat.id)
        await message.reply_text(messages.CLASSES_UNAVAILABLE)
        return
    if not classes:
        await message.reply_text(messages.NO_CLASSES)
        return
    lines = ["📅 Your upcoming classes", ""]
    for c in classes[:10]:
        lines.append(f"• {c.get('title', 'Class')} - {c.get('next_session') or 'schedule pending'}")
    await message.reply_text("\n".join(lines))


async def fallback_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(messages.FALLBACK)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Last line of defence: log any unexpected exception so it is never silent."""
    logger.error("Unhandled exception while processing an update", exc_info=context.error)


def register_handlers(application: Application, settings: Settings, backend: WebBackendClient) -> None:
    application.bot_data["settings"] = settings
    application.bot_data["backend"] = backend

    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("start", start_command, filters=private))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("id", id_command))
    application.add_handler(CommandHandler("myclasses", myclasses_command, filters=private))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & private, fallback_text))
    application.add_error_handler(on_error)
