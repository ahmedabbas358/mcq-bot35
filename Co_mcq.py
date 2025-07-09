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
from telegram.error import TelegramError

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
        r"(?:Q[.:)]?\s*)?(?P<q>.+?)\s*(?P<opts>(?:[A-J1-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j0-9])",
        re.S | re.I,
    ),
    re.compile(
        r"(?:س[.:)]?\s*)?(?P<q>.+?)\s*(?P<opts>(?:[أ-ي١-٩][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:الإجابة|الإجابة\s+الصحيحة)[:：]?\s*(?P<ans>[أ-ي١-٩])",
        re.S | re.I,
    ),
    re.compile(
        r"(?P<q>.+?)\s*(?P<opts>(?:\s*[A-Za-zء-ي0-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|الإجابة|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9])",
        re.S | re.I,
    ),
]

# الرسائل المترجمة
TEXTS = {
    "start": {"en": "🤖 Hi! Choose an option:", "ar": "🤖 أهلاً! اختر من القائمة:"},
    "help": {
        "en": "🆘 Usage:\n- Send MCQ in private.\n- To publish in a channel: use 🔄 or /setchannel.\n- In groups: reply or mention @bot.\nExample:\nQ: Capital of France?\nA) Paris\nB) London\nAnswer: A",
        "ar": "🆘 كيفية الاستخدام:\n- في الخاص: أرسل السؤال بصيغة Q:/س:.\n- للنشر في قناة: استخدم 🔄 أو /setchannel.\n- في المجموعات: رُدّ على البوت أو اذكر @البوت.\nمثال:\nس: عاصمة فرنسا؟\nأ) باريس\nب) لندن\nالإجابة: أ",
    },
    "new": {"en": "📩 Send your MCQ now!", "ar": "📩 أرسل سؤال MCQ الآن!"},
    "stats": {
        "en": "📊 Private: {pr} questions.\n🏷️ Channel: {ch} posts.",
        "ar": "📊 في الخاص: {pr} سؤال.\n🏷️ في القنوات: {ch} منشور.",
    },
    "queue_full": {"en": "🚫 Queue full, send fewer questions.", "ar": "🚫 القائمة ممتلئة، أرسل أقل."},
    "no_q": {"en": "❌ No questions detected. Ensure correct format.", "ar": "❌ لم يتم العثور على أسئلة. تأكد من الصيغة الصحيحة."},
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
    "not_admin": {"en": "⚠️ Bot must be an admin in the channel.", "ar": "⚠️ يجب أن يكون البوت مشرفًا في القناة."},
    "quiz_not_found": {"en": "❌ Quiz not found.", "ar": "❌ الاختبار غير موجود."},
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

async def close_db():
    """Close the database connection."""
    global _db_conn
    if _db_conn is not None:
        await _db_conn.close()
        _db_conn = None

async def schedule_cleanup():
    """Periodically clean up unused database entries."""
    max_retries = 3
    retry_count = 0
    while True:
        try:
            await asyncio.sleep(86400)  # يوم واحد
            conn = await get_db()
            await conn.execute(
                "DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent > 0)"
            )
            await conn.execute("DELETE FROM user_stats WHERE sent = 0")
            await conn.commit()
            retry_count = 0  # إعادة تعيين عداد المحاولات
        except Exception as e:
            logger.warning(f"خطأ في التنظيف: {e}")
            retry_count += 1
            if retry_count >= max_retries:
                logger.error("تجاوز الحد الأقصى لإعادة المحاولة في التنظيف")
                break
            await asyncio.sleep(60)  # الانتظار قبل إعادة المحاولة

def parse_mcq(text):
    """Parse MCQ text into question, options, and correct answer index."""
    text = re.sub(r"\s*\n\s*", "\n", text.strip())
    logger.info(f"Parsing MCQ text: {text}")
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group("q").strip()
            raw = m.group("opts")
            opts = re.findall(r"[A-Za-zأ-ي0-9][).:]\s*(.+)", raw)
            ans = m.group("ans").strip()
            logger.info(f"Found question: {q}, options: {opts}, answer: {ans}")
            if len(opts) > 10:
                logger.warning(f"Too many options ({len(opts)}), max is 10")
                continue
            if ans in ARABIC_DIGITS:
                ans = ARABIC_DIGITS[ans]
            idx = EN_LETTERS.get(ans) or AR_LETTERS.get(ans)
            if idx is None:
                try:
                    idx = int(ans) - 1
                except ValueError:
                    logger.warning(f"Invalid answer format: {ans}")
                    continue
            if not (0 <= idx < len(opts)):
                logger.warning(f"Answer index out of range: {idx}, options: {len(opts)}")
                continue
            res.append((q, opts, idx))
    if not res:
        logger.warning("No valid MCQs found in the text.")
    return res

async def check_bot_admin(context, chat_id):
    """Check if the bot is an admin in the channel."""
    try:
        bot_id = context.bot.id
        admins = await context.bot.get_chat_administrators(chat_id)
        return any(admin.user.id == bot_id for admin in admins)
    except TelegramError as e:
        logger.warning(f"Failed to check admin status for chat {chat_id}: {e}")
        return False

async def process_queue(chat_id, context, user_id=None, is_private=False, quiz_id=None):
    """Process the send queue for a given chat."""
    conn = await get_db()
    max_retries = 3
    while send_queues[chat_id]:
        q, opts, idx = send_queues[chat_id].popleft()
        retry_count = 0
        while retry_count < max_retries:
            try:
                if not is_private and not await check_bot_admin(context, chat_id):
                    lang = context.user_data.get("lang", "ar")
                    await context.bot.send_message(chat_id, get_text("not_admin", lang))
                    return
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
                    except TelegramError as e:
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
                lang = context.user_data.get("lang", "ar")
                if "bot_username" not in context.bot_data:
                    context.bot_data["bot_username"] = (await context.bot.get_me()).username
                bot_username = context.bot_data["bot_username"]
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
                break  # الخروج من حلقة إعادة المحاولة بعد النجاح
            except TelegramError as e:
                logger.error(f"خطأ في معالجة الاستبيان: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(f"تجاوز الحد الأقصى لإعادة المحاولة للاستبيان: {q}")
                    lang = context.user_data.get("lang", "ar")
                    await context.bot.send_message(chat_id, get_text("invalid_format", lang))
                    break
                await asyncio.sleep(1)  # الانتظار قبل إعادة المحاولة
            except Exception as e:
                logger.error(f"خطأ غير متوقع في معالجة الاستبيان: {e}")
                break

async def enqueue_mcq(msg, context, override=None, is_private=False):
    """Enqueue an MCQ for processing."""
    uid = getattr(msg.from_user, "id", None)  # التحقق من وجود from_user
    conn = await get_db()
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone() if uid else None
    default_channel = row[0] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or msg.chat.id)
    lang = context.user_data.get("lang", "ar")
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
        asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=is_private))
    return found

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text messages."""
    msg = update.message
    if not msg or not (msg.text or msg.caption):
        return
    uid = getattr(msg.from_user, "id", None)
    if uid and time.time() - last_sent_time[uid] < 3:
        return
    if uid:
        last_sent_time[uid] = time.time()

    chat_type = msg.chat.type
    if chat_type == ChatType.PRIVATE:
        found = await enqueue_mcq(msg, context, is_private=True)
        if not found:
            lang = context.user_data.get("lang", "ar")
            await context.bot.send_message(msg.chat.id, get_text("no_q", lang))
        return

    content = (msg.text or msg.caption or "").lower()
    if "bot_username" not in context.bot_data:
        context.bot_data["bot_username"] = (await context.bot.get_me()).username
    bot_username = context.bot_data["bot_username"].lower()
    if chat_type in ["group", "supergroup"]:
        if (
            (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id)
            or re.search(fr"@{re.escape(bot_username)}", content)
        ):
            found = await enqueue_mcq(msg, context, is_private=False)
            if not found:
                lang = context.user_data.get("lang", "ar")
                await context.bot.send_message(msg.chat.id, get_text("no_q", lang))
        return

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle channel posts."""
    post = update.channel_post
    if not post:
        return
    conn = await get_db()
    if not await check_bot_admin(context, post.chat.id):
        lang = "ar"
        try:
            await context.bot.send_message(post.chat.id, get_text("not_admin", lang))
        except TelegramError as e:
            logger.warning(f"فشل إرسال رسالة إلى القناة: {e}")
        return
    await conn.execute(
        "INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)",
        (post.chat.id, post.chat.title or ""),
    )
    await conn.commit()
    found = await enqueue_mcq(post, context, is_private=False)
    if not found:
        lang = "ar"
        try:
            await context.bot.send_message(post.chat.id, get_text("no_q", lang))
        except TelegramError as e:
            logger.warning(f"فشل إرسال رسالة إلى القناة: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command."""
    lang = (getattr(update.effective_user, "language_code", "ar") or "ar")[:2]
    context.user_data["lang"] = lang
    args = context.args
    chat_type = update.effective_chat.type
    if args and args[0].startswith("quiz_"):
        quiz_id = args[0][5:]
        conn = await get_db()
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if not row:
            await update.message.reply_text(get_text("quiz_not_found", lang))
            return
        q, opts_str, idx = row
        opts = opts_str.split(":::")
        cid = update.effective_chat.id
        if len(opts) > 10:
            await update.message.reply_text(get_text("invalid_format", lang))
            return
        if chat_type == ChatType.CHANNEL:
            if not await check_bot_admin(context, cid):
                await update.message.reply_text(get_text("not_admin", lang))
                return
            send_queues[cid].append((q, opts, idx))
            asyncio.create_task(process_queue(cid, context, user_id=getattr(update.effective_user, "id", None), is_private=False, quiz_id=quiz_id))
        else:
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if not rows:
                await update.message.reply_text(get_text("no_channels", lang))
                return
            kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
            await update.message.reply_text(
                "اختر قناة لنشر الاختبار:", reply_markup=InlineKeyboardMarkup(kb)
            )
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
    lang = context.user_data.get("lang", "ar")
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
    lang = context.user_data.get("lang", "ar")
    if not context.args:
        await update.message.reply_text("❌ يرجى تقديم معرف الاختبار. مثال: /repost <quiz_id>")
        return
    quiz_id = context.args[0]
    conn = await get_db()
    row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
    if not row:
        await update.message.reply_text(get_text("quiz_not_found", lang))
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
    lang = context.user_data.get("lang", "ar")
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
            txt = get_text("quiz_not_found", lang)
    elif cmd.startswith("repost_to_"):
        quiz_id, cid = cmd.split("_")[2:]
        cid = int(cid)
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx = row
            opts = opts_str.split(":::")
            if not await check_bot_admin(context, cid):
                await update.callback_query.edit_message_text(get_text("not_admin", lang))
                return
            send_queues[cid].append((q, opts, idx))
            asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=False, quiz_id=quiz_id))
            txt = get_text("quiz_sent", lang)
        else:
            txt = get_text("quiz_not_found", lang)
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
    lang = context.user_data.get("lang", "ar")
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        txt = get_text("no_channels", lang)
    else:
        channels_list = "\n".join(f"- {t}: {cid}" for cid, t in rows)
        txt = get_text("channels_list", lang, channels=channels_list)
    await update.message.reply_text(txt)

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

    async def shutdown(application):
        await close_db()

    app.post_init = startup
    app.post_shutdown = shutdown

    logger.info("✅ Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
