"""
Message handlers for incoming text, group chats, channel posts, and uploaded files.
"""

import contextlib
import logging
from typing import Optional

try:
    from telegram import Message, Update
    from telegram.constants import ChatType
    from telegram.ext import ContextTypes
except ImportError:
    class _Mock:
        pass
    Message = _Mock
    Update = _Mock
    ChatType = _Mock
    ContextTypes = _Mock

from bot.config import (
    GROUP_AUTO_PARSE_MCQS,
    Target,
)
from bot.database import (
    DB,
    resolve_user_lang,
)
from bot.handlers.commands import run_ai_flow
from bot.i18n.texts import get_text
from bot.parsers.document_parser import read_telegram_file
from bot.parsers.mcq_parser import looks_like_mcq_batch
from bot.utils.helpers import (
    detect_inline_ai_request,
    extract_message_text,
    infer_lang,
    remove_bot_mentions,
    resolve_chat_title,
)
from bot.utils.queue_manager import enqueue_mcq

logger = logging.getLogger(__name__)


def message_targets_bot(message: Message, bot_id: int, bot_username: Optional[str]) -> bool:
    """Check if the message explicitly mentions or replies to the bot."""
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot_id:
        return True
    text = extract_message_text(message)
    if bot_username and f"@{bot_username.lower()}" in text.lower():
        return True
    return False


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle normal incoming text messages and captions."""
    if update.channel_post:
        return
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat:
        return
    raw_text = extract_message_text(message)
    if not raw_text:
        return

    lang = await resolve_user_lang(user.id, getattr(user, "language_code", None), raw_text) if user else infer_lang(None, raw_text)
    inline_request = detect_inline_ai_request(raw_text)

    # Private chat flow
    if chat.type == ChatType.PRIVATE:
        if inline_request and user:
            await run_ai_flow(
                message=message,
                context=context,
                owner_user_id=user.id,
                lang=lang,
                mode=inline_request[0],
                payload=inline_request[1],
            )
            return
        await enqueue_mcq(message, context, owner_user_id=user.id if user else 0, is_private=True, notify_fail=True)
        return

    # Group / Supergroup flow
    bot_username = context.bot_data.get("bot_username")
    bot_id = context.bot_data.get("bot_id")
    if not bot_username or not bot_id:
        me = await context.bot.get_me()
        bot_username = me.username
        bot_id = me.id
        context.bot_data["bot_username"] = bot_username
        context.bot_data["bot_id"] = bot_id

    targeted = message_targets_bot(message, bot_id, bot_username)
    cleaned_text = remove_bot_mentions(raw_text, bot_username) if targeted else raw_text
    inline_request = detect_inline_ai_request(cleaned_text)

    if not targeted:
        if GROUP_AUTO_PARSE_MCQS and inline_request and user:
            await run_ai_flow(
                message=message,
                context=context,
                owner_user_id=user.id,
                lang=lang,
                mode=inline_request[0],
                payload=inline_request[1],
                explicit_target=chat.id,
            )
            return
        if GROUP_AUTO_PARSE_MCQS and looks_like_mcq_batch(raw_text):
            await enqueue_mcq(
                message,
                context,
                explicit_target=chat.id,
                owner_user_id=user.id if user else 0,
                is_private=False,
                notify_fail=True,
                text_override=raw_text,
            )
            return
        return

    if inline_request and user:
        await run_ai_flow(
            message=message,
            context=context,
            owner_user_id=user.id,
            lang=lang,
            mode=inline_request[0],
            payload=inline_request[1],
            explicit_target=chat.id,
        )
        return

    await enqueue_mcq(
        message,
        context,
        explicit_target=chat.id,
        owner_user_id=user.id if user else 0,
        is_private=False,
        notify_fail=True,
        text_override=cleaned_text,
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle uploaded files (such as .txt files with questions or study material)."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not message.document or not chat:
        return

    doc = message.document
    filename = (doc.file_name or "").lower()
    if not (filename.endswith(".txt") or filename.endswith(".text") or filename.endswith(".csv")):
        return

    lang = await resolve_user_lang(user.id, getattr(user, "language_code", None), "") if user else "ar"
    status_msg = None
    with contextlib.suppress(Exception):
        status_msg = await message.reply_text(get_text("document_parsing", lang, filename=doc.file_name or "file"))

    text_content = await read_telegram_file(context.bot, doc.file_id, doc.file_name or "")
    if not text_content:
        if status_msg:
            with contextlib.suppress(Exception):
                await status_msg.edit_text(get_text("document_empty", lang))
        return

    # Check if caption had an AI instruction
    caption = (message.caption or "").strip()
    inline_request = detect_inline_ai_request(caption) if caption else None

    if inline_request and user:
        if status_msg:
            with contextlib.suppress(Exception):
                await status_msg.delete()
        await run_ai_flow(
            message=message,
            context=context,
            owner_user_id=user.id,
            lang=lang,
            mode=inline_request[0],
            payload=text_content,
            explicit_target=chat.id if chat.type != ChatType.PRIVATE else None,
        )
        return

    # Parse MCQs directly
    success = await enqueue_mcq(
        message,
        context,
        owner_user_id=user.id if user else 0,
        is_private=(chat.type == ChatType.PRIVATE),
        notify_fail=False,
        text_override=text_content,
    )
    if status_msg:
        with contextlib.suppress(Exception):
            if success:
                await status_msg.delete()
            else:
                await status_msg.edit_text(get_text("document_empty", lang))


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle posts in broadcast channels where the bot is administrator."""
    post = update.channel_post or update.edited_channel_post
    if not post:
        return
    text = extract_message_text(post)
    if not text:
        return

    lang = infer_lang(None, text)
    conn = await DB.conn()
    await conn.execute(
        "INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)",
        (post.chat.id, resolve_chat_title(post.chat)),
    )
    await conn.commit()

    inline_request = detect_inline_ai_request(text)
    if inline_request:
        await run_ai_flow(
            message=post,
            context=context,
            owner_user_id=0,
            lang=lang,
            mode=inline_request[0],
            payload=inline_request[1],
            explicit_target=post.chat.id,
        )
        return

    await enqueue_mcq(post, context, explicit_target=post.chat.id, owner_user_id=0, is_private=False, notify_fail=True)
