# -*- coding: utf-8 -*-

import os
import re
import logging
import asyncio
import hashlib
import time
from collections import defaultdict, deque
from functools import lru_cache

import aiosqlite
from telegram import (
    Update,
    Poll,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatType, ParseMode

# =================================================================================
# 1. CONFIGURATION & SETUP
# =================================================================================

# --- Logging Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- Environment Variables & Constants ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "stats.db")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1339627053"))

# --- Global Variables ---
send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)
_db_conn: aiosqlite.Connection = None
# A lock to prevent multiple processors for the same chat
processing_locks = defaultdict(asyncio.Lock)


# --- Character & Regex Definitions ---
ARABIC_DIGITS = {"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}
ARABIC_DIGITS.update({str(i): str(i) for i in range(10)})
EN_LETTERS = {chr(ord("A") + i): i for i in range(10)}
EN_LETTERS.update({chr(ord("a") + i): i for i in range(10)})
AR_LETTERS = {"أ": 0, "ب": 1, "ج": 2, "د": 3, "هـ": 4, "و": 5, "ز": 6, "ح": 7, "ط": 8, "ي": 9}

PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-J1-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j0-9])",
        re.S | re.I,
    ),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-ي١-٩][).:]\s*.+?(?:\n|$)){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-ي١-٩])",
        re.S | re.I,
    ),
    re.compile(
        r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|الإجابة|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9])",
        re.S | re.I,
    ),
]

# --- Localization (Text Strings) ---
TEXTS = {
    "start": "🤖 أهلاً بك! أنا بوت لإنشاء اختبارات MCQ. اختر من القائمة للمتابعة:",
    "help": """🆘 *كيفية الاستخدام:*

- *في الخاص:* أرسل سؤالك مباشرةً بالتنسيق الصحيح.
- *للنشر في قناة:* استخدم زر "🔄 نشر في قناة" أو الأمر /setchannel لتعيين قناة افتراضية.
- *في المجموعات:* قم بالرد على رسالة البوت أو اعمل له منشن (@username).

*مثال على التنسيق:*
س: ما هي عاصمة السودان؟
أ) أم درمان
ب) الخرطوم
ج) بحري
الإجابة: ب
""",
    "new": "📩 أرسل سؤالك الآن بالتنسيق الموضح في قسم المساعدة.",
    "stats": "📊 *إحصائياتك:*\n\n- أسئلة أرسلتها في الخاص: *{pr}*\n- إجمالي المنشورات في القنوات: *{ch}*",
    "no_q_found": "❌ لم يتم العثور على سؤال صالح في رسالتك. يرجى مراجعة تنسيق السؤال والمحاولة مرة أخرى.",
    "quiz_sent": "✅ تم إرسال الاختبار بنجاح!",
    "share_quiz": "📢 مشاركة الاختبار",
    "repost_quiz": "🔄 إعادة نشر الاختبار",
    "choose_channel_repost": "اختر القناة التي تود إعادة نشر الاختبار فيها:",
    "choose_channel_default": "اختر قناة لتعيينها كوجهة نشر افتراضية:",
    "channels_list": "📺 *قائمة القنوات المسجلة:*\n\n{channels}",
    "no_channels": "❌ لا توجد قنوات مسجلة حالياً. يجب أن يكون البوت عضواً في قناة وأن يتم إرسال منشور فيها أولاً.",
    "set_channel_success": "✅ تم تعيين القناة الافتراضية بنجاح: *{title}*",
    "admin_panel": "🔧 *لوحة تحكم المشرف*",
    "admin_stats": "📊 *إحصائيات كاملة:*\n\n- المستخدمون النشطون: *{users}*\n- القنوات النشطة: *{channels}*\n- المستخدمون المحظورون: *{banned}*",
    "download_db": "📂 تحميل قاعدة البيانات",
    "ban_user_prompt": "🚫 أرسل المعرف الرقمي (ID) للمستخدم الذي تريد حظره.",
    "unban_user_prompt": "✅ أرسل المعرف الرقمي (ID) للمستخدم الذي تريد رفع الحظر عنه.",
    "user_banned_success": "🚫 تم حظر المستخدم `{user_id}` بنجاح.",
    "user_unbanned_success": "✅ تم رفع الحظر عن المستخدم `{user_id}` بنجاح.",
    "invalid_user_id": "⚠️ المعرف الذي أدخلته غير صالح. يرجى إرسال معرف رقمي صحيح.",
    "user_already_banned": "⚠️ هذا المستخدم محظور بالفعل.",
    "user_not_banned": "⚠️ هذا المستخدم غير محظور.",
    "banned_notice": "🚫 أنت محظور من استخدام هذا البوت.",
    "error_occurred": "⚠️ حدث خطأ أثناء إرسال أحد الأسئلة. سيقوم البوت بإعادة المحاولة. إذا استمرت المشكلة، قد يكون السؤال غير صالح أو هناك مشكلة في القناة المستهدفة.",
}

(SELECT_ACTION, AWAIT_USER_ID_FOR_BAN, AWAIT_USER_ID_FOR_UNBAN) = range(3)

# =================================================================================
# 2. DATABASE MANAGEMENT
# =================================================================================

async def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH)
        _db_conn.row_factory = aiosqlite.Row
    return _db_conn

async def init_db():
    conn = await get_db()
    await conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent_private INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent_channel INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS quizzes (quiz_id TEXT PRIMARY KEY, question TEXT NOT NULL, options TEXT NOT NULL, correct_option_id INTEGER NOT NULL, author_id INTEGER, created_at INTEGER DEFAULT (strftime('%s', 'now')));
        CREATE TABLE IF NOT EXISTS default_channels (user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, title TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY);
    """)
    await conn.commit()
    logger.info("Database initialized successfully.")

async def is_user_banned(user_id: int) -> bool:
    conn = await get_db()
    cursor = await conn.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    return await cursor.fetchone() is not None

async def get_default_channel(user_id: int) -> int | None:
    conn = await get_db()
    cursor = await conn.execute("SELECT chat_id FROM default_channels WHERE user_id = ?", (user_id,))
    row = await cursor.fetchone()
    return row['chat_id'] if row else None

@lru_cache(maxsize=1)
async def get_known_channels() -> list:
    conn = await get_db()
    cursor = await conn.execute("SELECT chat_id, title FROM known_channels ORDER BY title")
    return await cursor.fetchall()

async def get_quiz_by_id(quiz_id: str) -> aiosqlite.Row | None:
    conn = await get_db()
    cursor = await conn.execute("SELECT * FROM quizzes WHERE quiz_id = ?", (quiz_id,))
    return await cursor.fetchone()

# =================================================================================
# 3. CORE LOGIC (PARSING & QUEUE PROCESSING)
# =================================================================================

def parse_mcq(text: str) -> list[tuple]:
    results = []
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            try:
                question = match.group("q").strip()
                options_raw = match.group("opts")
                options = [opt.strip() for opt in re.findall(r"[A-Za-zأ-ي0-9][).:]\s*(.+)", options_raw)]

                if not (2 <= len(options) <= 10): continue

                answer_char = match.group("ans").strip()
                if answer_char in ARABIC_DIGITS: answer_char = ARABIC_DIGITS[answer_char]

                correct_index = EN_LETTERS.get(answer_char.upper())
                if correct_index is None: correct_index = AR_LETTERS.get(answer_char)
                if correct_index is None: correct_index = int(answer_char) - 1

                if 0 <= correct_index < len(options):
                    results.append((question, options, correct_index))
            except (ValueError, IndexError) as e:
                logger.warning(f"Could not parse a potential MCQ block: {e}")
                continue
    return results

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """
    Robustly processes the message queue for a specific chat.
    This function is now designed to be resilient to errors and ensures it continues processing.
    """
    lock = processing_locks[chat_id]
    if lock.locked():
        # Another processor is already running for this chat, so we exit.
        return

    async with lock:
        while send_queues[chat_id]:
            q, opts, idx, author_id, original_chat_id = send_queues[chat_id].popleft()
            
            try:
                # --- Send Poll ---
                poll = await context.bot.send_poll(
                    chat_id=chat_id,
                    question=q,
                    options=opts,
                    type=Poll.QUIZ,
                    correct_option_id=idx,
                    is_anonymous=False,
                    explanation=f"مقدم من: @{(await context.bot.get_me()).username}",
                )
                
                quiz_id = hashlib.md5((q + '::'.join(opts)).encode('utf-8')).hexdigest()
                
                # --- Send Confirmation and Keyboard ---
                bot_username = (await context.bot.get_me()).username
                share_link = f"https://t.me/{bot_username}?start={quiz_id}"
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton(TEXTS["share_quiz"], url=share_link)],
                    [InlineKeyboardButton(TEXTS["repost_quiz"], callback_data=f"repost_{quiz_id}")]
                ])

                await context.bot.send_message(
                    chat_id,
                    text=TEXTS["quiz_sent"],
                    reply_to_message_id=poll.message_id,
                    reply_markup=keyboard
                )

                # --- Database Updates ---
                conn = await get_db()
                await conn.execute(
                    "INSERT OR IGNORE INTO quizzes (quiz_id, question, options, correct_option_id, author_id) VALUES (?, ?, ?, ?, ?)",
                    (quiz_id, q, '::'.join(opts), idx, author_id),
                )
                
                is_private = (chat_id == original_chat_id)
                if is_private:
                    await conn.execute("INSERT OR IGNORE INTO user_stats(user_id) VALUES (?)", (author_id,))
                    await conn.execute("UPDATE user_stats SET sent_private = sent_private + 1 WHERE user_id = ?", (author_id,))
                else:
                    await conn.execute("INSERT OR IGNORE INTO channel_stats(chat_id) VALUES (?)", (chat_id,))
                    await conn.execute("UPDATE channel_stats SET sent_channel = sent_channel + 1 WHERE chat_id = ?", (chat_id,))
                
                await conn.commit()

            except TelegramError as e:
                # This catches API-specific errors (e.g., bot blocked, chat not found)
                logger.error(f"Telegram API error while sending to {chat_id}: {e}", exc_info=True)
                # Re-queue the failed item to try again later.
                send_queues[chat_id].appendleft((q, opts, idx, author_id, original_chat_id))
                try:
                    # Notify the original user about the failure
                    await context.bot.send_message(original_chat_id, TEXTS['error_occurred'])
                except Exception as notify_error:
                    logger.error(f"Failed to notify user {original_chat_id} about an error: {notify_error}")
                # Break the loop to prevent retrying the same failing item immediately.
                # The next message will trigger the processor again.
                break 
            
            except Exception as e:
                # This is a catch-all for any other unexpected errors (e.g., database issues)
                logger.error(f"An unexpected error occurred in process_queue for chat {chat_id}: {e}", exc_info=True)
                # Also re-queue and notify
                send_queues[chat_id].appendleft((q, opts, idx, author_id, original_chat_id))
                try:
                    await context.bot.send_message(original_chat_id, TEXTS['error_occurred'])
                except Exception as notify_error:
                    logger.error(f"Failed to notify user {original_chat_id} about an error: {notify_error}")
                break

            finally:
                # Add a small delay to avoid hitting rate limits
                await asyncio.sleep(1.2)


async def enqueue_mcq(update: Update, context: ContextTypes.DEFAULT_TYPE, target_chat_id: int | None = None):
    msg = update.effective_message
    user = update.effective_user
    
    if await is_user_banned(user.id):
        await msg.reply_text(TEXTS["banned_notice"])
        return

    final_chat_id = target_chat_id or await get_default_channel(user.id) or msg.chat.id
    
    text_content = msg.text or msg.caption or ""
    parsed_mcqs = parse_mcq(text_content)

    if not parsed_mcqs:
        await msg.reply_text(TEXTS["no_q_found"])
        return

    for q, opts, idx in parsed_mcqs:
        send_queues[final_chat_id].append((q, opts, idx, user.id, msg.chat.id))

    # Trigger the processor. The lock inside process_queue will prevent race conditions.
    asyncio.create_task(process_queue(final_chat_id, context))


# =================================================================================
# 4. TELEGRAM HANDLERS (No changes in this section, kept for completeness)
# =================================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = context.args
    if args and len(args[0]) == 32:
        quiz_id = args[0]
        quiz = await get_quiz_by_id(quiz_id)
        if quiz:
            q, opts_str, idx, author = quiz['question'], quiz['options'], quiz['correct_option_id'], quiz['author_id']
            opts = opts_str.split(':::')
            send_queues[user.id].append((q, opts, idx, author, user.id))
            asyncio.create_task(process_queue(user.id, context))
        else:
            await update.message.reply_text(TEXTS["no_q_found"])
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 سؤال جديد", callback_data="new_quiz"), InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
        [InlineKeyboardButton("🔄 نشر في قناة", callback_data="publish_channel"), InlineKeyboardButton("📘 المساعدة", callback_data="help")],
    ])
    await update.message.reply_text(TEXTS["start"], reply_markup=keyboard)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TEXTS["help"], parse_mode=ParseMode.MARKDOWN)

async def set_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channels = await get_known_channels()
    if not channels:
        await update.message.reply_text(TEXTS["no_channels"])
        return
    
    keyboard = [[InlineKeyboardButton(ch['title'], callback_data=f"set_default_{ch['chat_id']}")] for ch in channels]
    await update.message.reply_text(TEXTS["choose_channel_default"], reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
        [InlineKeyboardButton("📂 تحميل DB", callback_data="admin_download_db")],
        [InlineKeyboardButton("🚫 حظر مستخدم", callback_data="admin_ban_user")],
        [InlineKeyboardButton("✅ رفع الحظر", callback_data="admin_unban_user")],
    ])
    await update.message.reply_text(TEXTS["admin_panel"], reply_markup=keyboard)
    return SELECT_ACTION

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if time.time() - last_sent_time[msg.from_user.id] < 2: return
    last_sent_time[msg.from_user.id] = time.time()

    if msg.chat.type == ChatType.PRIVATE:
        await enqueue_mcq(update, context)
    elif msg.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        bot_username = (await context.bot.get_me()).username
        if (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or \
           (msg.text and f"@{bot_username}" in msg.text):
            await enqueue_mcq(update, context)

async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post: return
    
    conn = await get_db()
    await conn.execute(
        "INSERT OR REPLACE INTO known_channels(chat_id, title) VALUES (?, ?)",
        (post.chat.id, post.chat.title or f"Channel {post.chat.id}"),
    )
    await conn.commit()
    get_known_channels.cache_clear()
    
    await enqueue_mcq(update, context, target_chat_id=post.chat.id)

async def main_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cmd, *params = query.data.split('_')
    user_id = query.from_user.id
    conn = await get_db()

    if cmd == "new":
        await query.edit_message_text(TEXTS["new"])
    elif cmd == "help":
        await query.edit_message_text(TEXTS["help"], parse_mode=ParseMode.MARKDOWN)
    elif cmd == "stats":
        user_cursor = await conn.execute("SELECT sent_private FROM user_stats WHERE user_id = ?", (user_id,))
        user_stats = await user_cursor.fetchone()
        channel_cursor = await conn.execute("SELECT SUM(sent_channel) as total FROM channel_stats")
        channel_stats = await channel_cursor.fetchone()
        pr_count = user_stats['sent_private'] if user_stats else 0
        ch_count = channel_stats['total'] if channel_stats and channel_stats['total'] else 0
        await query.edit_message_text(TEXTS["stats"].format(pr=pr_count, ch=ch_count), parse_mode=ParseMode.MARKDOWN)
    elif cmd == "publish":
        channels = await get_known_channels()
        if not channels:
            await query.edit_message_text(TEXTS["no_channels"])
            return
        keyboard = [[InlineKeyboardButton(ch['title'], callback_data=f"repost_{params[0]}_{ch['chat_id']}")] for ch in channels]
        await query.edit_message_text(TEXTS["choose_channel_repost"], reply_markup=InlineKeyboardMarkup(keyboard))
    elif cmd == "set":
        chat_id = int(params[1])
        cursor = await conn.execute("SELECT title FROM known_channels WHERE chat_id = ?", (chat_id,))
        channel = await cursor.fetchone()
        if channel:
            await conn.execute("INSERT OR REPLACE INTO default_channels (user_id, chat_id, title) VALUES (?, ?, ?)",
                               (user_id, chat_id, channel['title']))
            await conn.commit()
            await query.edit_message_text(TEXTS["set_channel_success"].format(title=channel['title']), parse_mode=ParseMode.MARKDOWN)
    elif cmd == "repost":
        quiz_id = params[0]
        if len(params) > 1:
            chat_id = int(params[1])
            quiz = await get_quiz_by_id(quiz_id)
            if quiz:
                q, opts_str, idx = quiz['question'], quiz['options'], quiz['correct_option_id']
                send_queues[chat_id].append((q, opts_str.split(':::'), idx, user_id, query.message.chat.id))
                asyncio.create_task(process_queue(chat_id, context))
                await query.edit_message_text(TEXTS["quiz_sent"])
        else:
            channels = await get_known_channels()
            if not channels:
                await query.edit_message_text(TEXTS["no_channels"])
                return
            keyboard = [[InlineKeyboardButton(ch['title'], callback_data=f"repost_{quiz_id}_{ch['chat_id']}")] for ch in channels]
            await query.edit_message_text(TEXTS["choose_channel_repost"], reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cmd = query.data
    conn = await get_db()

    if cmd == "admin_stats":
        users_c = await (await conn.execute("SELECT COUNT(*) FROM user_stats")).fetchone()
        channels_c = await (await conn.execute("SELECT COUNT(*) FROM channel_stats")).fetchone()
        banned_c = await (await conn.execute("SELECT COUNT(*) FROM banned_users")).fetchone()
        await query.edit_message_text(TEXTS["admin_stats"].format(users=users_c[0], channels=channels_c[0], banned=banned_c[0]), parse_mode=ParseMode.MARKDOWN)
        return SELECT_ACTION
    elif cmd == "admin_download_db":
        await query.message.reply_document(document=open(DB_PATH, 'rb'))
        return SELECT_ACTION
    elif cmd == "admin_ban_user":
        await query.edit_message_text(TEXTS["ban_user_prompt"])
        return AWAIT_USER_ID_FOR_BAN
    elif cmd == "admin_unban_user":
        await query.edit_message_text(TEXTS["unban_user_prompt"])
        return AWAIT_USER_ID_FOR_UNBAN
    return ConversationHandler.END

async def ban_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_ban = int(update.message.text)
        conn = await get_db()
        if await is_user_banned(user_id_to_ban):
            await update.message.reply_text(TEXTS["user_already_banned"])
        else:
            await conn.execute("INSERT INTO banned_users (user_id) VALUES (?)", (user_id_to_ban,))
            await conn.commit()
            await update.message.reply_text(TEXTS["user_banned_success"].format(user_id=user_id_to_ban))
    except ValueError:
        await update.message.reply_text(TEXTS["invalid_user_id"])
    return ConversationHandler.END

async def unban_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id_to_unban = int(update.message.text)
        conn = await get_db()
        if not await is_user_banned(user_id_to_unban):
            await update.message.reply_text(TEXTS["user_not_banned"])
        else:
            await conn.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id_to_unban,))
            await conn.commit()
            await update.message.reply_text(TEXTS["user_unbanned_success"].format(user_id=user_id_to_unban))
    except ValueError:
        await update.message.reply_text(TEXTS["invalid_user_id"])
    return ConversationHandler.END

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("تم إلغاء الإجراء.")
    return ConversationHandler.END

# =================================================================================
# 5. APPLICATION LIFECYCLE
# =================================================================================

async def post_init_hook(application: Application):
    await init_db()
    logger.info("Bot is ready and database is connected.")

async def post_shutdown_hook(application: Application):
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        logger.info("Database connection closed.")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.critical("FATAL: TELEGRAM_BOT_TOKEN environment variable not set.")
        return

    builder = Application.builder().token(TELEGRAM_BOT_TOKEN)
    builder.post_init(post_init_hook)
    builder.post_shutdown(post_shutdown_hook)
    app = builder.build()

    admin_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_command)],
        states={
            SELECT_ACTION: [CallbackQueryHandler(admin_callback_handler, pattern="^admin_")],
            AWAIT_USER_ID_FOR_BAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, ban_user_handler)],
            AWAIT_USER_ID_FOR_UNBAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, unban_user_handler)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
        per_message=False
    )
    
    app.add_handler(admin_conv_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setchannel", set_channel_command))
    
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, text_message_handler))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.CAPTION), channel_post_handler))
    app.add_handler(MessageHandler(filters.REPLY | filters.Regex(re.compile(r'@\w*bot', re.IGNORECASE)), text_message_handler))

    app.add_handler(CallbackQueryHandler(main_callback_handler, pattern="^(?!admin_)"))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
