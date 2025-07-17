import os
import re
import logging
import asyncio
import time
import hashlib
from collections import defaultdict, deque
import uvicorn

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
_db_conn: aiosqlite.Connection = None

# دعم الأرقام والحروف
ARABIC_DIGITS = {"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}
ARABIC_DIGITS.update({str(i): str(i) for i in range(10)})
EN_LETTERS = {chr(ord("A") + i): i for i in range(10)}
EN_LETTERS.update({chr(ord("a") + i): i for i in range(10)})
AR_LETTERS = {"أ": 0, "ب": 1, "ج": 2, "د": 3, "هـ": 4, "و": 5, "ز": 6, "ح": 7, "ط": 8, "ي": 9}

# أنماط regex لتحليل الأسئلة
PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-J1-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j0-9])",
        re.S,
    ),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-ي١-٩][).:]\s*.+?(?:\n|$)){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-ي١-٩])",
        re.S,
    ),
    re.compile(
        r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|الإجابة|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9])",
        re.S,
    ),
]

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
}

def get_text(key, lang, **kwargs):
    """Retrieve translated text with fallback to English."""
    return TEXTS[key].get(lang, TEXTS[key]["en"]).format(**kwargs)

async def get_db():
    """Get a singleton database connection."""
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH)
    return _db_conn

async def init_db(conn):
    """Initialize database tables."""
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

async def schedule_cleanup():
    """Periodically clean up unused database entries."""
    while True:
        try:
            await asyncio.sleep(86400)  # يوم واحد
            conn = await get_db()
            await conn.execute(
                "DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent > 0)"
            )
            await conn.execute("DELETE FROM user_stats WHERE sent = 0")
            await conn.commit()
        except Exception as e:
            logger.warning(f"خطأ في التنظيف: {e}")

def parse_mcq(text):
    """Parse MCQ text into question, options, and correct answer index."""
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group("q").strip()
            raw = m.group("opts")
            opts = re.findall(r"[A-Za-zأ-ي0-9][).:]\s*(.+)", raw)
            ans = m.group("ans").strip()
            if ans in ARABIC_DIGITS:
                ans = ARABIC_DIGITS[ans]
            idx = EN_LETTERS.get(ans) or AR_LETTERS.get(ans)
            if idx is None:
                try:
                    idx = int(ans) - 1
                except ValueError:
                    continue
            if not (0 <= idx < len(opts)):
                continue
            res.append((q, opts, idx))
    return res

async def process_queue(chat_id, context, user_id=None, is_private=False, quiz_id=None):
    """Process the send queue for a given chat."""
    conn = await get_db()
    while send_queues[chat_id]:
        q, opts, idx = send_queues[chat_id].popleft()
        try:
            # Send the poll
            poll = await context.bot.send_poll(
                chat_id,
                q,
                opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False,
            )
            await asyncio.sleep(0.5)
            # Delete the original message if needed
            msg_id = context.user_data.pop("message_to_delete", None)
            if msg_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception as e:
                    logger.warning(f"فشل حذف الرسالة: {e}")
            # Generate quiz ID if not provided
            if not quiz_id:
                quiz_id = hashlib.md5((q + ':::'.join(opts)).encode()).hexdigest()
                await conn.execute(
                    "INSERT OR IGNORE INTO quizzes (quiz_id, question, options, correct_option, user_id) VALUES (?, ?, ?, ?, ?)",
                    (quiz_id, q, ':::'.join(opts), idx, user_id),
                )
                await conn.commit()
            # Create inline buttons for sharing and reposting
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
            # Update stats
            if is_private:
                await conn.execute(
                    "INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES (?, 0)",
                    (user_id,),
                )
                await conn.execute(
                    "UPDATE user_stats SET sent = sent + 1 WHERE user_id = ?",
                    (user_id,),
                )
            else:
                await conn.execute(
                    "INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?, 0)",
                    (chat_id,),
                )
                await conn.execute(
                    "UPDATE channel_stats SET sent = sent + 1 WHERE chat_id = ?",
                    (chat_id,),
                )
            await conn.commit()
        except Exception as e:
            logger.error(f"خطأ في معالجة الاستبيان: {e}")
            # Re-queue the question in case of failure
            send_queues[chat_id].appendleft((q, opts, idx))
            break

async def enqueue_mcq(msg, context, override=None, is_private=False):
    """Enqueue an MCQ for processing."""
    uid = msg.from_user.id
    conn = await get_db()
    # Check for default channel
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone()
    default_channel = row[0] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or msg.chat.id)
    lang = (msg.from_user.language_code or "en")[:2]
    context.user_data["lang"] = lang
    if len(send_queues[cid]) > 50:
        await context.bot.send_message(cid, get_text("queue_full", lang))
        return False
    text = msg.text or msg.caption or ""
    blocks = [b for b in re.split(r"\n{2,}", text) if b.strip()]
    found = False
    for blk in blocks:
        for q, o, i in parse_mcq(blk):
            send_queues[cid].append((q, o, i))
            found = True
    if found:
        context.user_data["message_to_delete"] = msg.message_id
        asyncio.create_task(process_queue(cid, context, user_id=msg.from_user.id, is_private=is_private))
    return found

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        found = await enqueue_mcq(msg, context, is_private=True)
        if not found:
            lang = (msg.from_user.language_code or "en")[:2]
            await context.bot.send_message(msg.chat.id, get_text("no_q", lang))
        return

    content = (msg.text or msg.caption or "").lower()
    bot_username = (await context.bot.get_me()).username.lower()
    if chat_type in ["group", "supergroup"]:
        if (
            (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id)
            or content.strip().startswith(f"@{bot_username}")
        ):
            found = await enqueue_mcq(msg, context, is_private=False)
            if not found:
                lang = (msg.from_user.language_code or "en")[:2]
                await context.bot.send_message(msg.chat.id, get_text("no_q", lang))
        return

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            send_queues[update.effective_chat.id].append((q, opts, idx))
            asyncio.create_task(process_queue(update.effective_chat.id, context, user_id=update.effective_user.id, is_private=False, quiz_id=quiz_id))
        else:
            await update.message.reply_text(get_text("no_q", lang))
        return
    kb = [
        [InlineKeyboardButton("📝 سؤال جديد", callback_data="new")],
        [InlineKeyboardButton("🔄 نشر في قناة", callback_data="publish_channel")],
        [InlineKeyboardButton("📊 إحصائياتي", callback_data="stats")],
        [InlineKeyboardButton("📘 المساعدة", callback_data="help")],
        [InlineKeyboardButton("📺 القنوات", callback_data="channels")],
    ]
    await update.message.reply_text(
        get_text("start", lang), reply_markup=InlineKeyboardMarkup(kb)
    )

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a default channel for posting quizzes."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_channels", lang))
        return
    kb = [[InlineKeyboardButton(t, callback_data=f"set_default_{cid}")] for cid, t in rows]
    await update.message.reply_text(
        "اختر قناة لتعيينها كافتراضية:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def repost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Repost a quiz by ID."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    if not context.args:
        await update.message.reply_text("❌ يرجى تقديم معرف الاختبار. مثال: /repost <quiz_id>")
        return
    quiz_id = context.args[0]
    conn = await get_db()
    row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
    if not row:
        await update.message.reply_text(get_text("no_q", lang))
        return
    q, opts_str, idx = row
    opts = opts_str.split(":::")
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_channels", lang))
        return
    kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
    await update.message.reply_text(
        "اختر مكانًا لإعادة النشر:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline keyboard."""
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = context.user_data.get("lang", "en")
    conn = await get_db()
    txt = "⚠️ غير مدعوم"
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
        await update.callback_query.edit_message_text(
            "اختر قناة:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    elif cmd.startswith("choose_"):
        cid = int(cmd.split("_")[1])
        row = await (await conn.execute("SELECT title FROM known_channels WHERE chat_id=?", (cid,))).fetchone()
        if row:
            context.chat_data["target_channel"] = cid
            txt = f"✅ قناة محددة: {row[0]}. أرسل السؤال في الخاص.\n" + get_text(
                "private_channel_warning", lang
            )
        else:
            txt = "❌ غير موجود"
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
    elif cmd.startswith("repost_"):
        quiz_id = cmd.split("_")[1]
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx = row
            opts = opts_str.split(":::")
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if not rows:
                await update.callback_query.edit_message_text(get_text("no_channels", lang))
                return
            kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
            await update.callback_query.edit_message_text(
                "اختر مكانًا لإعادة النشر:", reply_markup=InlineKeyboardMarkup(kb)
            )
            return
        else:
            txt = get_text("no_q", lang)
    elif cmd.startswith("repost_to_"):
        quiz_id, cid = cmd.split("_")[2:]
        cid = int(cid)
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx = row
            opts = opts_str.split(":::")
            send_queues[cid].append((q, opts, idx))
            asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=False, quiz_id=quiz_id))
            txt = get_text("quiz_sent", lang)
        else:
            txt = get_text("no_q", lang)
    await update.callback_query.edit_message_text(txt)

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline queries."""
    q = update.inline_query.query
    if not q:
        return
    result = InlineQueryResultArticle(
        id="1",
        title="تحويل إلى MCQ",
        input_message_content=InputTextMessageContent(q),
    )
    await update.inline_query.answer([result])

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

def run_web_server():
    """Run the web server using Uvicorn without signal handlers."""
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(
        "Co_mcq:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        workers=1,
        log_level="info",
        access_log=False,
        install_signal_handlers=False  # الحل الرئيسي للمشكلة
    )

def main():
    """Main entry point for the bot."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("❌ Bot token not found. Set TELEGRAM_BOT_TOKEN environment variable.")
    
    app = Application.builder().token(token).build()

    # إضافة المعالجات
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("setchannel", set_channel))
    app.add_handler(CommandHandler("repost", repost))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(
        MessageHandler(
            filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption),
            handle_channel_post,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # تهيئة قاعدة البيانات وجدولة التنظيف
    async def startup(application):
        conn = await get_db()
        await init_db(conn)
        asyncio.create_task(schedule_cleanup())

    app.post_init = startup

    # تحديد طريقة التشغيل بناءً على البيئة
    if os.getenv("RAILWAY"):
        # على Railway: تشغيل خادم الويب في خيط منفصل
        import threading
        server_thread = threading.Thread(target=run_web_server, daemon=True)
        server_thread.start()
        logger.info("✅ Health check server running on port 8000")
        
        # بدء البوت في الخيط الرئيسي
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    else:
        # في البيئة المحلية: التشغيل العادي
        logger.info("✅ البوت يعمل...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
