"""
Message handlers for incoming text, group chats, channel posts, and uploaded files.
"""

import contextlib
import logging
import re
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
    get_user_settings,
    resolve_user_lang,
)
from bot.handlers.commands import run_ai_flow, send_text_reply
from bot.i18n.texts import get_text
from bot.keyboards.inline import (
    build_wizard_cancel_keyboard,
    build_wizard_choices_keyboard,
    build_wizard_completed_keyboard,
)
from bot.parsers.document_parser import read_telegram_file
from bot.parsers.mcq_parser import looks_like_mcq_batch
from bot.services.wizard_service import (
    QuestionWizard,
    cancel_wizard,
    finish_wizard_and_publish,
    get_wizard,
)
from bot.utils.helpers import (
    detect_inline_ai_request,
    extract_message_text,
    format_target_label,
    infer_lang,
    remove_bot_mentions,
    resolve_chat_title,
)
from bot.utils.queue_manager import enqueue_mcq, enqueue_quiz_items

logger = logging.getLogger(__name__)


def message_targets_bot(message: Message, bot_id: int, bot_username: Optional[str]) -> bool:
    """Check if the message explicitly mentions or replies to the bot."""
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot_id:
        return True
    text = extract_message_text(message)
    if bot_username and f"@{bot_username.lower()}" in text.lower():
        return True
    return False


async def handle_wizard_step(
    wizard: QuestionWizard,
    message: Message,
    raw_text: str,
    lang: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle text input during active question creation wizard."""
    if wizard.step == "question":
        wizard.question = raw_text.strip()
        wizard.step = "options"
        prompt = (
            "📝 *الخيارات المقترحة (الخطوة 2 من 3)*\n"
            "───────────────────────\n"
            f"السؤال: *{wizard.question}*\n\n"
            "أرسل الآن الخيارات المقترحة (كل خيار في سطر مستقل):\n\n"
            "💡 *مثال:*\n"
            "الخيار الأول\n"
            "الخيار الثاني\n"
            "الخيار الثالث\n"
            "الخيار الرابع"
            if lang == "ar"
            else
            "📝 *Options (Step 2 of 3)*\n"
            "───────────────────────\n"
            f"Question: *{wizard.question}*\n\n"
            "Send the choices now (one choice per line):\n\n"
            "💡 *Example:*\n"
            "Option 1\n"
            "Option 2\n"
            "Option 3\n"
            "Option 4"
        )
        await send_text_reply(message, prompt, reply_markup=build_wizard_cancel_keyboard(lang), parse_mode="Markdown")
        return

    if wizard.step == "options":
        raw_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        opts = []
        for line in raw_lines:
            cleaned = re.sub(r"^[A-Za-z0-9أ-ي\-\•\*\)\.\s]+[\)\.\:\-]\s*", "", line).strip()
            opts.append(cleaned if cleaned else line)
        if len(opts) < 2:
            warn = (
                "⚠️ يرجى إرسال خيارين على الأقل (كل خيار في سطر مستقل)."
                if lang == "ar"
                else "⚠️ Please provide at least 2 choices (one per line)."
            )
            await send_text_reply(message, warn, reply_markup=build_wizard_cancel_keyboard(lang))
            return
        if len(opts) > 10:
            opts = opts[:10]
        wizard.options = opts
        wizard.step = "correct"
        prompt = (
            "🎯 *تحديد الإجابة الصحيحة (الخطوة 3 من 3)*\n"
            "───────────────────────\n"
            f"السؤال: *{wizard.question}*\n\n"
            "اضغط على الخيار الصحيح من الأزرار أدناه:"
            if lang == "ar"
            else
            "🎯 *Select Correct Answer (Step 3 of 3)*\n"
            "───────────────────────\n"
            f"Question: *{wizard.question}*\n\n"
            "Tap the correct choice below:"
        )
        await send_text_reply(message, prompt, reply_markup=build_wizard_choices_keyboard(wizard.options, lang), parse_mode="Markdown")
        return

    if wizard.step == "explanation":
        wizard.explanation = raw_text.strip()
        await finish_wizard_and_publish(wizard, message, context)
        return


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
        if user:
            wizard = get_wizard(user.id)
            if wizard and wizard.chat_id == chat.id:
                await handle_wizard_step(wizard, message, raw_text, lang, context)
                return

        # Reply keyboard quick actions
        btn_clean = raw_text.strip()
        if btn_clean in {"✍️ إنشاء سؤال جديد", "✍️ Create Question"}:
            from bot.handlers.commands import create_handler
            await create_handler(update, context)
            return
        if btn_clean in {"🎯 بدء اختبار تفاعلي", "🎯 Start Exam"}:
            from bot.handlers.commands import exam_handler
            await exam_handler(update, context)
            return
        if btn_clean in {"🎓 بطاقة إنجازاتي", "🎓 My Report Card"}:
            from bot.handlers.commands import mystats_handler
            await mystats_handler(update, context)
            return
        if btn_clean in {"⚙️ الإعدادات", "⚙️ Settings"}:
            from bot.handlers.commands import settings_handler
            await settings_handler(update, context)
            return
        if btn_clean in {"📋 نموذج التنسيق", "📋 Copyable Template"}:
            from bot.handlers.commands import format_handler
            await format_handler(update, context)
            return
        if btn_clean in {"🤖 الذكاء الاصطناعي", "🤖 AI Generator"}:
            from bot.handlers.commands import ai_handler
            await ai_handler(update, context)
            return

        # Arabic commands support (with or without slash)
        first_token = raw_text.strip().lstrip("/").split()[0].lower() if raw_text.strip() else ""
        if raw_text.strip().startswith("/") or len(raw_text.strip().split()) == 1:
            from bot.handlers.commands import (
                ai_handler,
                create_handler,
                difficulty_handler,
                exam_handler,
                examples_handler,
                export_handler,
                format_handler,
                help_handler,
                language_handler,
                leaderboard_handler,
                menu_handler,
                mystats_handler,
                search_handler,
                settings_handler,
                start_handler,
                stats_handler,
                study_handler,
                tools_handler,
            )
            arabic_cmd_map = {
                "ابدأ": start_handler,
                "ابدا": start_handler,
                "مساعدة": help_handler,
                "تعليمات": help_handler,
                "قائمة": menu_handler,
                "اوامر": menu_handler,
                "أوامر": menu_handler,
                "احصائيات": stats_handler,
                "إحصائيات": stats_handler,
                "اعدادات": settings_handler,
                "إعدادات": settings_handler,
                "اختبار": exam_handler,
                "امتحان": exam_handler,
                "المتصدرين": leaderboard_handler,
                "شرف": leaderboard_handler,
                "تصدير": export_handler,
                "نموذج": format_handler,
                "تنسيق": format_handler,
                "إنشاء": create_handler,
                "انشاء": create_handler,
                "بناء": create_handler,
                "صعوبة": difficulty_handler,
                "المستوى": difficulty_handler,
                "مستوى": difficulty_handler,
                "بحث": search_handler,
                "انجازاتي": mystats_handler,
                "إنجازاتي": mystats_handler,
                "تقريري": mystats_handler,
                "مذاكرة": study_handler,
                "دراسة": study_handler,
                "ذكاء": ai_handler,
                "ادوات": tools_handler,
                "أدوات": tools_handler,
                "لغة": language_handler,
                "اللغة": language_handler,
                "امثلة": examples_handler,
                "أمثلة": examples_handler,
            }
            if first_token in arabic_cmd_map:
                await arabic_cmd_map[first_token](update, context)
                return

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
