"""
Main Telegram Bot Application Builder, lifecycle hooks, and runner.
"""

import asyncio
import contextlib
import logging
import os
import time

try:
    from telegram import Update
    from telegram.ext import (
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )
except ImportError:
    Update = None
    ApplicationBuilder = None
    CallbackQueryHandler = None
    CommandHandler = None
    MessageHandler = None
    filters = None

from bot.config import (
    CONCURRENT_UPDATES,
    ENABLE_WEB_PREVIEW,
    GLOBAL_SEND_LIMIT,
    LONG_POLL_TIMEOUT,
)
from bot.database import DB, init_db
from bot.handlers import (
    ai_handler,
    aidiag_handler,
    ask_handler,
    callback_query_handler,
    clearchannel_handler,
    controls_handler,
    delivery_handler,
    difficulty_handler,
    exam_handler,
    examples_handler,
    export_handler,
    format_handler,
    freeai_handler,
    freemodel_handler,
    freemodels_handler,
    funmode_handler,
    funrate_handler,
    funstyle_handler,
    handle_channel_post,
    handle_document,
    handle_text,
    health_handler,
    help_handler,
    language_handler,
    leaderboard_handler,
    menu_handler,
    provider_handler,
    providers_handler,
    publishhere_handler,
    quizify_handler,
    setchannel_handler,
    setcount_handler,
    setmodel_handler,
    settings_handler,
    sharemode_handler,
    specialty_handler,
    start_handler,
    stats_handler,
    study_handler,
    toggleai_handler,
    toggleconfirm_handler,
    toggledelete_handler,
    toggleexplain_handler,
    tool_handler,
    tools_handler,
)
from bot.utils.helpers import log_memory_usage
from bot.utils.queue_manager import sender_tasks
from bot.web.app import keep_alive

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def schedule_cleanup() -> None:
    """Daily cleanup task to prune quizzes older than 90 days and log memory."""
    while True:
        await asyncio.sleep(86400)
        try:
            conn = await DB.conn()
            ninety_days_ago = int(time.time()) - (90 * 24 * 60 * 60)
            await conn.execute("DELETE FROM quizzes WHERE created_at > 0 AND created_at < ?", (ninety_days_ago,))
            await conn.commit()
            log_memory_usage()
            logger.info("Daily cleanup completed successfully")
        except Exception as exc:
            logger.exception("Cleanup task error: %s", exc)


async def post_init(application) -> None:
    """Initialize DB schema, web preview server, and schedule tasks upon startup."""
    await init_db()
    me = await application.bot.get_me()
    application.bot_data["bot_username"] = me.username or ""
    application.bot_data["bot_id"] = me.id
    if ENABLE_WEB_PREVIEW:
        with contextlib.suppress(Exception):
            keep_alive()
    application.create_task(schedule_cleanup())
    logger.info("Bot initialized as @%s (ID: %s)", me.username, me.id)


async def on_shutdown(application) -> None:
    """Graceful cancellation of queue workers and closing DB pool."""
    logger.info("Shutting down bot gracefully...")
    all_tasks = [task for tasks in sender_tasks.values() for task in tasks]
    for task in all_tasks:
        task.cancel()
    if all_tasks:
        await asyncio.gather(*all_tasks, return_exceptions=True)
    await DB.close()
    logger.info("Shutdown completed successfully")


def build_application():
    """Build and configure the Telegram application with all handlers."""
    if ApplicationBuilder is None:
        raise RuntimeError("python-telegram-bot is missing. Please run: pip install -r requirements.txt")

    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is missing!")

    builder = ApplicationBuilder().token(token).post_init(post_init)
    if hasattr(builder, "concurrent_updates"):
        builder = builder.concurrent_updates(CONCURRENT_UPDATES)
    if hasattr(builder, "connection_pool_size"):
        builder = builder.connection_pool_size(max(GLOBAL_SEND_LIMIT, CONCURRENT_UPDATES * 2))
    if hasattr(builder, "pool_timeout"):
        builder = builder.pool_timeout(60.0)
    if hasattr(builder, "connect_timeout"):
        builder = builder.connect_timeout(30.0)
    if hasattr(builder, "read_timeout"):
        builder = builder.read_timeout(float(LONG_POLL_TIMEOUT + 5))
    if hasattr(builder, "write_timeout"):
        builder = builder.write_timeout(60.0)
    if hasattr(builder, "post_shutdown"):
        builder = builder.post_shutdown(on_shutdown)
    app = builder.build()

    # Core Navigation & Info Commands
    app.add_handler(CommandHandler(["start", "ابدأ", "ابدا"], start_handler))
    app.add_handler(CommandHandler(["help", "مساعدة", "تعليمات"], help_handler))
    app.add_handler(CommandHandler(["menu", "قائمة", "اوامر"], menu_handler))
    app.add_handler(CommandHandler(["stats", "احصائيات", "إحصائيات"], stats_handler))
    app.add_handler(CommandHandler(["settings", "controls", "اعدادات", "إعدادات"], settings_handler))
    app.add_handler(CommandHandler("health", health_handler))
    app.add_handler(CommandHandler(["examples", "امثلة", "أمثلة"], examples_handler))

    # Channel & Target Configuration
    app.add_handler(CommandHandler(["setchannel", "تحديد_القناة"], setchannel_handler))
    app.add_handler(CommandHandler(["publishhere", "انشر_هنا"], publishhere_handler))
    app.add_handler(CommandHandler(["clearchannel", "الغاء_القناة"], clearchannel_handler))
    app.add_handler(CommandHandler("toggledelete", toggledelete_handler))

    # AI Configuration & Profiles
    app.add_handler(CommandHandler("toggleai", toggleai_handler))
    app.add_handler(CommandHandler("setmodel", setmodel_handler))
    app.add_handler(CommandHandler("providers", providers_handler))
    app.add_handler(CommandHandler("provider", provider_handler))
    app.add_handler(CommandHandler("freeai", freeai_handler))
    app.add_handler(CommandHandler("aidiag", aidiag_handler))
    app.add_handler(CommandHandler("freemodels", freemodels_handler))
    app.add_handler(CommandHandler("freemodel", freemodel_handler))
    app.add_handler(CommandHandler("setcount", setcount_handler))
    app.add_handler(CommandHandler(["difficulty", "صعوبة", "المستوى"], difficulty_handler))

    # User Preferences
    app.add_handler(CommandHandler(["language", "lang", "لغة"], language_handler))
    app.add_handler(CommandHandler("specialty", specialty_handler))
    app.add_handler(CommandHandler("delivery", delivery_handler))
    app.add_handler(CommandHandler("sharemode", sharemode_handler))
    app.add_handler(CommandHandler("toggleexplain", toggleexplain_handler))
    app.add_handler(CommandHandler("toggleconfirm", toggleconfirm_handler))

    # Study Tools & AI Generation
    app.add_handler(CommandHandler(["tools", "أدوات", "ادوات"], tools_handler))
    app.add_handler(CommandHandler("tool", tool_handler))
    app.add_handler(CommandHandler(["ask", "اسأل", "سؤال"], ask_handler))
    app.add_handler(CommandHandler(["study", "مذاكرة", "دراسة"], study_handler))
    app.add_handler(CommandHandler(["ai", "ذكاء"], ai_handler))
    app.add_handler(CommandHandler("quizify", quizify_handler))

    # Fun Breaks
    app.add_handler(CommandHandler("funmode", funmode_handler))
    app.add_handler(CommandHandler("funrate", funrate_handler))
    app.add_handler(CommandHandler("funstyle", funstyle_handler))

    # Advanced Features: Export, Exam, Format & Leaderboard
    app.add_handler(CommandHandler(["export", "تصدير"], export_handler))
    app.add_handler(CommandHandler(["exam", "test", "اختبار", "امتحان"], exam_handler))
    app.add_handler(CommandHandler(["leaderboard", "top", "المتصدرين", "شرف"], leaderboard_handler))
    app.add_handler(CommandHandler(["format", "نموذج", "تنسيق"], format_handler))

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # Channel Posts
    app.add_handler(MessageHandler(filters.UpdateType.CHANNEL_POST & filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST & filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))

    # File / Document Uploads (e.g. .txt files with questions)
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    # Regular Messages & Captions
    app.add_handler(MessageHandler((filters.TEXT | filters.Caption) & ~filters.COMMAND, handle_text))

    return app


def main() -> None:
    """Start polling loop for bot."""
    app = build_application()
    logger.info("MCQ Bot v2.0 is starting long polling...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
        timeout=LONG_POLL_TIMEOUT,
    )


if __name__ == "__main__":
    main()
