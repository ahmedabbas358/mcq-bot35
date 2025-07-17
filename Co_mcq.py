import os
import re
import logging
import asyncio
import time
import hashlib
from collections import defaultdict, deque

import aiosqlite
from telegram import (
    Update,
    Poll,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ChatType
from keep_alive import keep_alive
keep_alive()

# إعداد اللوجر
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# تعريف المتغيرات العامة
DB_PATH = os.getenv("DB_PATH", "stats.db")
send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)
MAX_QUEUE_SIZE = 50
_db_conn: aiosqlite.Connection = None

# دعم الأرقام والحروف
ARABIC_DIGITS = {"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}
ARABIC_DIGITS.update({str(i): str(i) for i in range(10)})
EN_LETTERS = {chr(ord("A") + i): i for i in range(10)}
EN_LETTERS.update({chr(ord("a") + i): i for i in range(10)})
AR_LETTERS = {"أ": 0, "ب": 1, "ج": 2, "د": 3, "ه": 4, "و": 5, "ز": 6, "ح": 7, "ط": 8, "ي": 9}

# تعريف بادئات الأسئلة وكلمات الإجابة
QUESTION_PREFIXES = ["Q", "Question", "س", "سؤال"]
ANSWER_KEYWORDS = ["Answer", "Ans", "Correct Answer", "الإجابة", "الجواب", "الإجابة الصحيحة"]

# الرسائل المترجمة
TEXTS = {
    "start": {"en": "🤖 Hi! Choose an option:", "ar": "🤖 أهلاً! اختر من القائمة:"},
    "help": {
        "en": "🆘 Usage:\n- Send MCQ in private.\n- To publish in a channel: use 🔄 or /setchannel.\n- In groups: reply or mention @bot.\nExample:\nQ: ...\nA) ...\nB) ...\nAnswer: A",
        "ar": "🆘 كيفية الاستخدام:\n- في الخاص: أرسل السؤال بصيغة Q:/س:.\n- للنشر في قناة: استخدم 🔄 أو /setchannel.\n- في المجموعات: رُدّ على البوت أو اذكر @البوت.\nمثال:\nس: ...\nأ) ...\nب) ...\nالإجابة: أ",
    },
    "new": {"en": "📩 Send your MCQ now!", "ar": "📩 أرسل سؤال MCQ الآن!"},
    "stats": {
        "en": "📊 Private: {pr} questions.\n🏷️ Channel: {ch} posts.",
        "ar": "📊 في الخاص: {pr} سؤال.\n🏷️ في القنوات: {ch} منشور.",
    },
    "queue_full": {"en": "🚫 Queue full, send fewer questions.", "ar": "🚫 القائمة ممتلئة، أرسل أقل."},
    "no_q": {"en": "❌ No questions detected.", "ar": "❌ لم يتم العثور على أسئلة."},
    "invalid_format": {"en": "⚠️ Invalid format.", "ar": "⚠️ صيغة غير صحيحة."},
    "quiz_sent": {"en": "✅ Quiz sent!", "ar": "✅ تم إرسال الاختبار!"},
    "share_quiz": {"en": "📢 Share Quiz", "ar": "📢 مشاركة الاختبار"},
    "repost_quiz": {"en": "🔄 Repost Quiz", "ar": "🔄 إعادة نشر الاختبار"},
    "channels_list": {"en": "📺 Channels:\n{channels}", "ar": "📺 القنوات:\n{channels}"},
    "no_channels": {"en": "❌ No channels found.", "ar": "❌ لا توجد قنوات."},
    "private_channel_warning": {
        "en": "⚠️ Ensure the bot is an admin in the private channel.",
        "ar": "⚠️ تأكد أن البوت مشرف في القناة الخاصة.",
    },
    "set_channel_success": {"en": "✅ Default channel set: {title}", "ar": "✅ تم تعيين القناة الافتراضية: {title}"},
    "no_channel_selected": {"en": "❌ No channel selected.", "ar": "❌ لم يتم اختيار قناة."},
    "health_check": {
        "en": "🟢 Bot is running!\nStart time: {start_time}",
        "ar": "🟢 البوت يعمل الآن!\nوقت البدء: {start_time}"
    },
}

def get_text(key: str, lang: str, **kwargs) -> str:
    """Retrieve translated text with fallback to English."""
    return TEXTS[key].get(lang, TEXTS[key]["en"]).format(**kwargs)

async def get_db() -> aiosqlite.Connection:
    """Get a singleton database connection."""
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH)
    return _db_conn

async def init_db() -> None:
    """Initialize database tables."""
    conn = await get_db()
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS quizzes (quiz_id TEXT PRIMARY KEY, question TEXT, options TEXT, correct_option INTEGER, user_id INTEGER)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS default_channels (user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)"
    )
    await conn.commit()
    logger.info("✅ Database initialized successfully")

async def schedule_cleanup() -> None:
    """Periodically clean up unused database entries."""
    while True:
        try:
            await asyncio.sleep(86400)  # 1 day
            conn = await get_db()
            await conn.execute(
                "DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent > 0)"
            )
            await conn.execute("DELETE FROM user_stats WHERE sent = 0")
            await conn.commit()
            logger.info("✅ Database cleanup completed")
        except Exception as e:
            logger.error(f"Error in cleanup: {e}")
            await asyncio.sleep(3600)  # Wait 1 hour before retrying

def parse_single_mcq(block: str) -> tuple[str, list[str], int] | None:
    """Parse a single MCQ block into question, options, and correct answer index."""
    lines = [line.strip() for line in block.split('\n') if line.strip()]
    question = None
    options = []
    answer_line = None

    for line in lines:
        if any(line.startswith(prefix) for prefix in QUESTION_PREFIXES):
            question = line
            continue
        if question and any(keyword in line for keyword in ANSWER_KEYWORDS):
            answer_line = line
            break
        match = re.match(r'^(\w+)[).:]?\s*(.+)', line)
        if match:
            label, text = match.groups()
            options.append((label, text))

    if not question or not options or not answer_line:
        return None

    answer_match = re.search(r'(?:' + '|'.join(ANSWER_KEYWORDS) + r')[:：]?\s*(\w+)', answer_line)
    if not answer_match:
        return None
    answer_label = answer_match.group(1)

    label_to_index = {}
    for i, (label, _) in enumerate(options):
        norm_label = label.strip().upper() if label.isalpha() and label[0] in 'A-Za-z' else label
        label_to_index[norm_label] = i

    norm_answer_label = answer_label.strip().upper() if answer_label.isalpha() and answer_label[0] in 'A-Za-z' else answer_label
    if norm_answer_label not in label_to_index:
        return None

    correct_idx = label_to_index[norm_answer_label]
    question_text = re.sub(r'^' + '|'.join(QUESTION_PREFIXES) + r'[.:]?\s*', '', question).strip()
    options_text = [text for _, text in options]
    return question_text, options_text, correct_idx

def parse_mcq(text: str) -> list[tuple[str, list[str], int]]:
    """Parse MCQ text into a list of (question, options, correct_idx)."""
    blocks = [b.strip() for b in re.split(r'\n{2,}', text) if b.strip()]
    results = []
    for block in blocks:
        parsed = parse_single_mcq(block)
        if parsed:
            results.append(parsed)
    return results

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int = None, is_private: bool = False) -> None:
    """Process the send queue for a given chat."""
    conn = await get_db()
    while send_queues[chat_id]:
        q, opts, idx, quiz_id, msg_id = send_queues[chat_id].popleft()
        try:
            poll = await context.bot.send_poll(
                chat_id,
                q,
                opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False,
            )
            if msg_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception as e:
                    logger.warning(f"Failed to delete message: {e}")

            if not quiz_id:
                quiz_id = hashlib.md5((q + ':::'.join(opts)).encode()).hexdigest()
                await conn.execute(
                    "INSERT OR IGNORE INTO quizzes (quiz_id, question, options, correct_option, user_id) VALUES (?, ?, ?, ?, ?)",
                    (quiz_id, q, ':::'.join(opts), idx, user_id),
                )
                await conn.commit()

            lang = context.user_data.get("lang", "en")
            bot_username = (await context.bot.get_me()).username
            share_link = f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
            keyboard = [
                [InlineKeyboardButton(get_text("share_quiz", lang), url=share_link)],
                [InlineKeyboardButton(get_text("repost_quiz", lang), callback_data=f"repost_{quiz_id}")]
            ]
            await context.bot.send_message(
                chat_id,
                get_text("quiz_sent", lang),
                reply_markup=InlineKeyboardMarkup(keyboard),
                reply_to_message_id=poll.message_id
            )

            if is_private:
                await conn.execute("INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES (?, 0)", (user_id,))
                await conn.execute("UPDATE user_stats SET sent = sent + 1 WHERE user_id = ?", (user_id,))
            else:
                await conn.execute("INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES892 (?, 0)", (chat_id,))
                await conn.execute("UPDATE channel_stats SET sent = sent + 1 WHERE chat_id = ?", (chat_id,))
            await conn.commit()
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error processing quiz: {e}")
            send_queues[chat_id].appendleft((q, opts, idx, quiz_id, msg_id))
            break

async def enqueue_mcq(msg: Update.message, context: ContextTypes.DEFAULT_TYPE, override: int = None, is_private: bool = False) -> bool:
    """Enqueue an MCQ for processing."""
    uid = msg.from_user.id
    conn = await get_db()
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone()
    default_channel = row[0] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or msg.chat.id)
    lang = (msg.from_user.language_code or "en")[:2]
    context.user_data["lang"] = lang

    if len(send_queues[cid]) >= MAX_QUEUE_SIZE:
        await context.bot.send_message(cid, get_text("queue_full", lang))
        return False

    text = msg.text or msg.caption or ""
    results = parse_mcq(text)
    if not results:
        if is_private:
            await context.bot.send_message(msg.chat.id, get_text("no_q", lang))
        return False

    for question, options, correct_idx in results:
        quiz_id = hashlib.md5((question + ':::' + ':::'.join(options)).encode()).hexdigest()
        send_queues[cid].append((question, options, correct_idx, quiz_id, msg.message_id if is_private else None))
    await process_queue(cid, context, user_id=uid, is_private=is_private)
    return True

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    msg = update.message
    if not msg or not (msg.text or msg.caption):
        return
    uid = msg.from_user.id
    if time.time() - last_sent_time[uid] < 3:
        return
    last_sent_time[uid] = time.time()

    chat_type = msg.chat.type
    if chat_type == ChatType.PRIVATE:
        await enqueue_mcq(msg, context, is_private=True)
        return

    content = (msg.text or msg.caption or "").lower()
    bot_username = (await context.bot.get_me()).username.lower()
    if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        is_reply_to_bot = msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id
        is_mention = f"@{bot_username}" in content
        if is_reply_to_bot or is_mention:
            await enqueue_mcq(msg, context, is_private=False)

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle channel posts."""
    post = update.channel_post
    if not post:
        return
    conn = await get_db()
    await conn.execute(
        "INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)",
        (post.chat.id, post.chat.title or ""),
    )
    await conn.commit()
    found = await enqueue_mcq(post, context, is_private=False)
    if not found:
        lang = (post.from_user.language_code or "en")[:2]
        await context.bot.send_message(post.chat.id, get_text("no_q", lang))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /start command."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    context.user_data["lang"] = lang
    args = context.args
    if args and args[0].startswith("quiz_"):
        quiz_id = args[0][5:]
        conn = await get_db()
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx = row
            opts = opts_str.split(":::")
            send_queues[update.effective_chat.id].append((q, opts, idx, quiz_id, None))
            await process_queue(update.effective_chat.id, context, user_id=update.effective_user.id, is_private=False)
        else:
            await update.message.reply_text(get_text("no_q", lang))
        return

    kb = [
        [InlineKeyboardButton("📝 New Question", callback_data="new")],
        [InlineKeyboardButton("🔄 Publish to Channel", callback_data="publish_channel")],
        [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
        [InlineKeyboardButton("📘 Help", callback_data="help")],
        [InlineKeyboardButton("📺 Channels", callback_data="channels")],
    ]
    await update.message.reply_text(get_text("start", lang), reply_markup=InlineKeyboardMarkup(kb))

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set a default channel for posting quizzes."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_channels", lang))
        return
    kb = [[InlineKeyboardButton(t, callback_data=f"set_default_{cid}")] for cid, t in rows]
    await update.message.reply_text("Choose a channel to set as default:", reply_markup=InlineKeyboardMarkup(kb))

async def repost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repost a quiz by ID."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    if not context.args:
        await update.message.reply_text("❌ Please provide a quiz ID. Example: /repost <quiz_id>")
        return
    quiz_id = context.args[0]
    conn = await get_db()
    row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
    if not row:
        await update.message.reply_text(get_text("no_q", lang))
        return
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_channels", lang))
        return
    kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
    await update.message.reply_text("Choose where to repost:", reply_markup=InlineKeyboardMarkup(kb))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries from inline keyboard."""
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = context.user_data.get("lang", "en")
    conn = await get_db()
    txt = "⚠️ Unsupported"

    if cmd == "help":
        txt = get_text("help", lang)
    elif cmd == "new":
        txt = get_text("new", lang)
    elif cmd == "stats":
        r = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (uid,))).fetchone()
        s = await (await conn.execute("SELECT SUM(sent) FROM channel_stats")).fetchone()
        txt = get_text("stats", lang, pr=r[0] if r else 0, ch=s[0] if s else 0)
    elif cmd == "channels":
        rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        if not rows:
            txt = get_text("no_channels", lang)
        else:
            channels_list = "\n".join(f"- {t}: {cid}" for cid, t in rows)
            txt = get_text("channels_list", lang, channels=channels_list)
    elif cmd == "publish_channel":
        rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        if not rows:
            await update.callback_query.edit_message_text(get_text("no_channels", lang))
            return
        kb = [[InlineKeyboardButton(t, callback_data=f"choose_{cid}")] for cid, t in rows]
        await update.callback_query.edit_message_text("Choose a channel:", reply_markup=InlineKeyboardMarkup(kb))
        return
    elif cmd.startswith("choose_"):
        cid = int(cmd.split("_")[1])
        row = await (await conn.execute("SELECT title FROM known_channels WHERE chat_id=?", (cid,))).fetchone()
        if row:
            context.chat_data["target_channel"] = cid
            txt = f"✅ Channel selected: {row[0]}. Send the question in private.\n" + get_text("private_channel_warning", lang)
        else:
            txt = "❌ Not found"
    elif cmd.startswith("set_default_"):
        cid = int(cmd.split("_")[2])
        row = await (await conn.execute("SELECT title FROM known_channels WHERE chat_id=?", (cid,))).fetchone()
        if row:
            await conn.execute(
                "INSERT OR REPLACE INTO default_channels (user_id, chat_id, title) VALUES (?, ?, ?)",
                (uid, cid, row[0]),
            )
            await conn.commit()
            txt = get_text("set_channel_success", lang, title=row[0])
        else:
            txt = get_text("no_channels", lang)
    elif cmd.startswith("repost_") or cmd.startswith("repost_to_"):
        quiz_id = cmd.split("_")[1] if cmd.startswith("repost_") else cmd.split("_")[2]
        if cmd.startswith("repost_"):
            row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
            if row:
                rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
                if not rows:
                    await update.callback_query.edit_message_text(get_text("no_channels", lang))
                    return
                kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
                await update.callback_query.edit_message_text("Choose where to repost:", reply_markup=InlineKeyboardMarkup(kb))
                return
            else:
                txt = get_text("no_q", lang)
        else:
            cid = int(cmd.split("_")[3])
            row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
            if row:
                q, opts_str, idx = row
                opts = opts_str.split(":::")
                send_queues[cid].append((q, opts, idx, quiz_id, None))
                await process_queue(cid, context, user_id=uid, is_private=False)
                txt = get_text("quiz_sent", lang)
            else:
                txt = get_text("no_q", lang)
    await update.callback_query.edit_message_text(txt)

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries."""
    q = update.inline_query.query
    if not q:
        return
    result = InlineQueryResultArticle(
        id="1",
        title="Convert to MCQ",
        input_message_content=InputTextMessageContent(q),
    )
    await update.inline_query.answer([result])

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the /channels command."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        txt = get_text("no_channels", lang)
    else:
        channels_list = "\n".join(f"- {t}: {cid}" for cid, t in rows)
        txt = get_text("channels_list", lang, channels=channels_list)
    await update.message.reply_text(txt)

async def health_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send health status message on startup."""
    try:
        me = await context.bot.get_me()
        logger.info(f"✅ Bot is running: {me.username}")
        developer_id = os.getenv("DEVELOPER_ID")
        if developer_id:
            current_time = time.strftime("%Y-%m-%d %H:%M:%S")
            lang = "ar"
            await context.bot.send_message(
                chat_id=developer_id,
                text=get_text("health_check", lang, start_time=current_time)
            )
    except Exception as e:
        logger.error(f"Health check failed: {e}")

async def post_init(application: Application) -> None:
    """Initialize the application on startup."""
    await init_db()
    asyncio.create_task(schedule_cleanup())
    await health_check(application)

async def post_shutdown(application: Application) -> None:
    """Cleanup on shutdown."""
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None
    logger.info("✅ Database connection closed")

def main() -> None:
    """Main entry point for the bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("❌ Bot token not found. Set TELEGRAM_BOT_TOKEN environment variable.")

    application = (
        Application.builder()
        .token(token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler("channels", channels_command))
    application.add_handler(CommandHandler("setchannel", set_channel))
    application.add_handler(CommandHandler("repost", repost))
    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(InlineQueryHandler(inline_query))
    application.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("✅ Bot is starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
