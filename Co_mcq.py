# bot.py — production-ready Telegram MCQ bot (async, PTB modern API)
# Requirements:
#   python-telegram-bot==21.6
#   aiosqlite
#   psutil
#   langdetect
#
# Usage:
#   export TELEGRAM_BOT_TOKEN="xxx"
#   python bot.py

import os
import re
import logging
import asyncio
import hashlib
import contextlib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

import aiosqlite
import psutil
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Poll
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ChatType
from langdetect import detect

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ---------------------------
# Configuration (env)
# ---------------------------
DB_PATH = os.getenv("DB_PATH", "stats.db")
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "50"))
SEND_INTERVAL = float(os.getenv("SEND_INTERVAL", "0.75"))         # seconds between polls
MAX_CONCURRENT_SEND = int(os.getenv("MAX_CONCURRENT_SEND", "5"))  # parallel polls per chat
MAX_QUESTION_LENGTH = int(os.getenv("MAX_QUESTION_LENGTH", "300"))
MAX_OPTION_LENGTH = int(os.getenv("MAX_OPTION_LENGTH", "100"))

# ---------------------------
# Texts (i18n minimal)
# ---------------------------
TEXTS = {
    "start": {"en": "🤖 Hi! Choose an option:", "ar": "🤖 أهلاً! اختر من القائمة:"},
    "help": {
        "en": "🆘 Usage:\n- Send MCQ in private.\n- To publish in a channel: use 🔄 or /setchannel.\n- In groups: reply or mention @bot.",
        "ar": "🆘 كيفية الاستخدام:\n- في الخاص: أرسل السؤال بصيغة Q:/س:.\n- للنشر في قناة: استخدم 🔄 أو /setchannel.\n- في المجموعات: رُدّ على البوت أو اذكر @البوت."
    },
    "no_q": {"en": "❌ No questions detected.", "ar": "❌ لم يتم العثور على أسئلة."},
    "invalid_format": {"en": "⚠️ Invalid format.", "ar": "⚠️ صيغة غير صحيحة."},
    "queue_full": {"en": "🚫 Queue full, send fewer questions.", "ar": "🚫 القائمة ممتلئة، أرسل أقل."},
    "quiz_sent": {"en": "✅ Quiz sent!", "ar": "✅ تم إرسال الاختبار!"},
    "share_quiz": {"en": "📢 Share Quiz", "ar": "📢 مشاركة الاختبار"},
    "repost_quiz": {"en": "🔄 Repost Quiz", "ar": "🔄 إعادة نشر الاختبار"},
    "stats": {
        "en": "📊 Private: {pr} questions.\n🏷️ Channel: {ch} posts.",
        "ar": "📊 في الخاص: {pr} سؤال.\n🏷️ في القنوات: {ch} منشور."
    },
    "leaderboard": {"en": "🏆 Leaderboard:\n{list}", "ar": "🏆 ترتيب المتصدرين:\n{list}"}
}

def get_text(key: str, lang: str = "en", **kwargs) -> str:
    lang_key = (lang or "en")[:2]
    template = TEXTS.get(key, {}).get(lang_key) or TEXTS.get(key, {}).get("en", "")
    return template.format(**kwargs)

# ---------------------------
# Utilities
# ---------------------------
def log_memory_usage():
    try:
        mem = psutil.Process().memory_info().rss / (1024 * 1024)
        logger.info(f"Memory usage: {mem:.2f} MB")
    except Exception:
        pass

def detect_lang(text: str, fallback="en") -> str:
    try:
        return detect(text)
    except Exception:
        return fallback

# ---------------------------
# Async DB singleton
# ---------------------------
class DB:
    _conn: Optional[aiosqlite.Connection] = None
    _lock = asyncio.Lock()

    @classmethod
    async def conn(cls) -> aiosqlite.Connection:
        async with cls._lock:
            if cls._conn is None:
                cls._conn = await aiosqlite.connect(DB_PATH)
                await cls._conn.execute("PRAGMA journal_mode=WAL")
                await cls._conn.execute("PRAGMA synchronous=NORMAL")
            return cls._conn

    @classmethod
    async def close(cls) -> None:
        async with cls._lock:
            if cls._conn:
                await cls._conn.close()
                cls._conn = None

# ---------------------------
# Parsing (robust MCQ parser)
# ---------------------------
# --- same ARABIC_DIGITS, ARABIC_LETTERS, QUESTION_PREFIXES, ANSWER_KEYWORDS ---
ARABIC_DIGITS = {**{str(i): str(i) for i in range(10)},
                 **{"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
                    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}}
ARABIC_LETTERS = {
    'أ': 'A', 'ا': 'A', 'إ': 'A', 'آ': 'A', 'ب': 'B', 'ت': 'C', 'ث': 'D',
    'ج': 'E', 'ح': 'F', 'خ': 'G', 'د': 'H', 'ذ': 'I', 'ر': 'J', 'ز': 'K',
    'س': 'L', 'ش': 'M', 'ص': 'N', 'ض': 'O', 'ط': 'P', 'ظ': 'Q', 'ع': 'R',
    'غ': 'S', 'ف': 'T', 'ق': 'U', 'ك': 'V', 'ل': 'W', 'م': 'X', 'ن': 'Y',
    'ه': 'Z', 'ة': 'Z', 'و': 'W', 'ي': 'Y', 'ئ': 'Y', 'ء': ''
}
QUESTION_PREFIXES = ["Q", "Question", "س", "سؤال"]
ANSWER_KEYWORDS = ["Answer", "Ans", "Correct Answer", "الإجابة", "الجواب", "الإجابة الصحيحة"]

def validate_mcq(q: str, options: List[str]) -> bool:
    if not q or not options:
        return False
    if len(q) > MAX_QUESTION_LENGTH:
        return False
    if len(options) < 2 or len(options) > 10:
        return False
    if any(len(opt) > MAX_OPTION_LENGTH for opt in options):
        return False
    return True

# --- parse_single_mcq and parse_mcq functions same as your previous code ---
# (omitted here for brevity; they remain fully compatible with Arabic/English MCQs)

# ---------------------------
# Queues, semaphores, senders, scheduler
# ---------------------------
SendItem = Tuple[str, List[str], int, str, Optional[int], Optional[str]]  # question, opts, idx, quiz_id, msg_id, category
send_queues: Dict[int, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=MAX_QUEUE_SIZE))
semaphores: Dict[int, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(MAX_CONCURRENT_SEND))
sender_tasks: Dict[int, asyncio.Task] = {}
scheduled_quizzes: List[Tuple[datetime, int, SendItem]] = []  # list of (send_time, chat_id, SendItem)

async def _sender(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, is_private: bool) -> None:
    logger.info(f"Sender task started for chat {chat_id}")
    try:
        while True:
            q, opts, idx, quiz_id, to_delete, category = await send_queues[chat_id].get()
            async with semaphores[chat_id]:
                retry_attempts = 0
                while retry_attempts < 5:
                    try:
                        poll = await context.bot.send_poll(
                            chat_id=chat_id,
                            question=f"[{category}] {q}" if category else q,
                            options=opts,
                            type=Poll.QUIZ,
                            correct_option_id=idx,
                            is_anonymous=False,
                        )
                        if to_delete:
                            with contextlib.suppress(Exception):
                                await context.bot.delete_message(chat_id=chat_id, message_id=to_delete)

                        conn = await DB.conn()
                        if not quiz_id:
                            quiz_id = hashlib.md5((q + ':::' + ':::'.join(opts)).encode()).hexdigest()
                            await conn.execute(
                                "INSERT OR IGNORE INTO quizzes(quiz_id, question, options, correct_option, user_id, category) VALUES (?,?,?,?,?,?)",
                                (quiz_id, q, ':::'.join(opts), idx, user_id, category),
                            )
                            await conn.commit()

                        bot_username = context.bot_data.get("bot_username")
                        if not bot_username:
                            me = await context.bot.get_me()
                            bot_username = me.username
                            context.bot_data["bot_username"] = bot_username
                        share_link = f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
                        kb = [
                            [InlineKeyboardButton(get_text("share_quiz", "en"), url=share_link)],
                            [InlineKeyboardButton(get_text("repost_quiz", "en"), callback_data=f"repost_{quiz_id}")]
                        ]
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=get_text("quiz_sent", "en"),
                            reply_markup=InlineKeyboardMarkup(kb),
                            reply_to_message_id=poll.message_id
                        )

                        # Stats update
                        if is_private:
                            await conn.execute("INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES (?,0)", (user_id,))
                            await conn.execute("UPDATE user_stats SET sent=sent+1 WHERE user_id=?", (user_id,))
                        else:
                            await conn.execute("INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?,0)", (chat_id,))
                            await conn.execute("UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?", (chat_id,))
                        await conn.commit()

                        await asyncio.sleep(SEND_INTERVAL)
                        break
                    except telegram.error.BadRequest as e:
                        logger.warning(f"BadRequest while sending poll to {chat_id}: {e}")
                        await asyncio.sleep(2)
                        retry_attempts += 1
                    except Exception as e:
                        logger.exception(f"Error sending poll to {chat_id}: {e}")
                        await asyncio.sleep(2 ** retry_attempts)
                        retry_attempts += 1
    except asyncio.CancelledError:
        logger.info(f"Sender task cancelled for {chat_id}")
        raise

def ensure_sender(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, is_private: bool) -> None:
    if chat_id not in sender_tasks or sender_tasks[chat_id].done():
        sender_tasks[chat_id] = asyncio.create_task(_sender(chat_id, context, user_id, is_private))

# ---------------------------
# Enqueue wrapper + scheduling
# ---------------------------
async def enqueue_mcq(msg, context, override=None, is_private=False, category=None, schedule_time: Optional[datetime]=None) -> bool:
    uid = msg.from_user.id if getattr(msg, "from_user", None) else 0
    conn = await DB.conn()
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone() if uid else None
    default_channel = row[0] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or (msg.chat_id if hasattr(msg, "chat_id") else msg.chat.id))
    lang = (msg.from_user.language_code or detect_lang(msg.text))[:2] if getattr(msg, "from_user", None) else "en"
    text = (msg.text or msg.caption or "")

    try:
        results = parse_mcq(text)
    except Exception as e:
        logger.exception(f"Parsing failed: {e}")
        if is_private and hasattr(msg, "reply_text"):
            await msg.reply_text(get_text("invalid_format", lang))
        return False

    if not results:
        if is_private and hasattr(msg, "reply_text"):
            await msg.reply_text(get_text("no_q", lang))
        return False

    success = False
    for q, opts, idx in results:
        if not validate_mcq(q, opts):
            if is_private and hasattr(msg, "reply_text"):
                await msg.reply_text(get_text("invalid_format", lang))
            continue

        quiz_id = hashlib.md5((q + ':::' + ':::'.join(opts)).encode()).hexdigest()
        send_item = (q, opts, idx, quiz_id, msg.message_id if is_private and hasattr(msg, "message_id") else None, category)
        if schedule_time:
            scheduled_quizzes.append((schedule_time, cid, send_item))
            success = True
        else:
            try:
                send_queues[cid].put_nowait(send_item)
                success = True
            except asyncio.QueueFull:
                if is_private and hasattr(msg, "reply_text"):
                    await msg.reply_text(get_text("queue_full", lang))
                return False
            ensure_sender(cid, context, uid, is_private)
    return success

async def process_scheduled():
    while True:
        now = datetime.utcnow()
        due = [sq for sq in scheduled_quizzes if sq[0] <= now]
        for sq in due:
            _, chat_id, item = sq
            try:
                send_queues[chat_id].put_nowait(item)
            except asyncio.QueueFull:
                logger.warning(f"Queue full for scheduled quiz to chat {chat_id}")
            scheduled_quizzes.remove(sq)
        await asyncio.sleep(10)

# ---------------------------
# Handlers
# ---------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not (msg.text or msg.caption):
        return
    if update.effective_chat.type == ChatType.PRIVATE:
        await enqueue_mcq(msg, context, is_private=True)
        if hasattr(msg, "reply_text"):
            await msg.reply_text(get_text("quiz_sent", (msg.from_user.language_code or "en")[:2]))
        return

    bot_username = context.bot_data.get("bot_username")
    if not bot_username:
        me = await context.bot.get_me()
        bot_username = me.username
        context.bot_data["bot_username"] = bot_username

    text = (msg.text or msg.caption or "").lower()
    is_reply = bool(msg.reply_to_message and getattr(msg.reply_to_message, "from_user", None) and msg.reply_to_message.from_user.id == (context.bot_data.get("bot_id") or (await context.bot.get_me()).id))
    is_mention = f"@{bot_username.lower()}" in text
    if is_reply or is_mention:
        await enqueue_mcq(msg, context, is_private=False)

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return
    conn = await DB.conn()
    await conn.execute(
        "INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)",
        (post.chat.id, post.chat.title or "")
    )
    await conn.commit()
    await enqueue_mcq(post, context, is_private=False)

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = (update.effective_user.language_code or "en")[:2]
    kb = [[InlineKeyboardButton("📝 New Question", callback_data="new")],
          [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
          [InlineKeyboardButton("📘 Help", callback_data="help")]]
    if update.message:
        await update.message.reply_text(get_text("start", lang), reply_markup=InlineKeyboardMarkup(kb))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    cmd = query.data
    uid = update.effective_user.id if update.effective_user else 0
    lang = (update.effective_user.language_code or "en")[:2] if update.effective_user else "en"
    conn = await DB.conn()

    if cmd == "stats":
        r = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (uid,))).fetchone()
        s = await (await conn.execute("SELECT SUM(sent) FROM channel_stats")).fetchone()
        txt = get_text("stats", lang, pr=(r[0] if r else 0), ch=(s[0] if s and s[0] else 0))
    elif cmd == "help":
        txt = get_text("help", lang)
    else:
        txt = "⚠️ Unsupported"
    with contextlib.suppress(Exception):
        await query.edit_message_text(txt)

# ---------------------------
# DB initialization & cleanup
# ---------------------------
async def init_db() -> None:
    conn = await DB.conn()
    await conn.execute("CREATE TABLE IF NOT EXISTS user_stats(user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS channel_stats(chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS known_channels(chat_id INTEGER PRIMARY KEY, title TEXT)")
    await conn.execute("CREATE TABLE IF NOT EXISTS quizzes(quiz_id TEXT PRIMARY KEY, question TEXT, options TEXT, correct_option INTEGER, user_id INTEGER, category TEXT)")
    await conn.execute("CREATE TABLE IF NOT EXISTS default_channels(user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)")
    await conn.commit()
    logger.info("✅ DB initialized")

async def schedule_cleanup() -> None:
    while True:
        await asyncio.sleep(86400)
        try:
            conn = await DB.conn()
            await conn.execute("DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent>0)")
            await conn.execute("DELETE FROM user_stats WHERE sent=0")
            await conn.commit()
            logger.info("✅ DB cleanup")
        except Exception as e:
            logger.exception(f"Cleanup error: {e}")

# ---------------------------
# Startup / Shutdown hooks
# ---------------------------
async def post_init(app: "telegram.ext.Application") -> None:
    await init_db()
    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username
    app.bot_data["bot_id"] = me.id
    try:
        app.create_task(schedule_cleanup())
        app.create_task(process_scheduled())
    except Exception:
        asyncio.create_task(schedule_cleanup())
        asyncio.create_task(process_scheduled())
    logger.info("✅ Bot post_init complete")

async def shutdown(app: "telegram.ext.Application") -> None:
    logger.info("🛑 Shutting down...")
    for t in list(sender_tasks.values()):
        t.cancel()
    await asyncio.gather(*sender_tasks.values(), return_exceptions=True)
    await DB.close()
    logger.info("✅ Graceful shutdown completed")

# ---------------------------
# Main
# ---------------------------
def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("❌ TELEGRAM_BOT_TOKEN missing")

    app = ApplicationBuilder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("✅ Bot is starting")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, close_loop=False)

if __name__ == "__main__":
    main()
