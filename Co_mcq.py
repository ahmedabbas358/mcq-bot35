import os
import re
import logging
import asyncio
import time
import hashlib
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
from async_lru import alru_cache

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
ADMIN_IDS = {1339627053}  # قائمة معرفات المشرفين

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
        "en": "🆘 Usage- Send MCQ in private.\n- To publish: /setchannel.\n- In groups: reply or @bot.\nQ: 2+2?\nA) 3\nB) 4\nAnswer: B",
        "ar": "🆘 أرسل MCQ في الخاص.\n- للنشر: /setchannel.\n- في المجموعات: رد أو @البوت.\nس: ٢+٢؟\nأ) ٣\nب) ٤\nالإجابة: ب",
    },
    "new": {"en": "📩 Send your MCQ now!", "ar": "📩 أرسل سؤالك الآن!"},
    "stats": {"en": "📊 Private: {pr}\nChannels: {ch}", "ar": "📊 خاص: {pr}\nقنوات: {ch}"},
    "no_q": {"en": "❌ No questions found.", "ar": "❌ لا أسئلة."},
    "invalid_format": {"en": "⚠️ Invalid format.", "ar": "⚠️ صيغة غير صحيحة."},
    "quiz_sent": {"en": "✅ Quiz sent!", "ar": "✅ تم إرسال السؤال!"},
    "share_quiz": {"en": "📢 Share Quiz", "ar": "📢 مشاركة السؤال"},
    "repost_quiz": {"en": "🔄 Repost Quiz", "ar": "🔄 إعادة النشر"},
    "channels_list": {"en": "📺 Channels:\n{channels}", "ar": "📺 القنوات:\n{channels}"},
    "no_channels": {"en": "❌ No channels.", "ar": "❌ لا قنوات."},
    "private_channel_warning": {"en": "⚠️ Ensure bot is admin in private channel.", "ar": "⚠️ تأكد أن البوت مشرف في القناة."},
    "set_channel_success": {"en": "✅ Channel set: {title}", "ar": "✅ تم تعيين القناة: {title}"},
    "admin_panel": {"en": "🔧 Admin Panel", "ar": "🔧 لوحة الإدارة"},
    "stats_full": {"en": "📊 Users: {users}\nChannels: {channels}\nBanned: {banned}", "ar": "📊 مستخدمون: {users}\nقنوات: {channels}\nمحظورون: {banned}"},
    "download_db": {"en": "📂 Download DB", "ar": "📂 تحميل قاعدة البيانات"},
    "search_quiz": {"en": "🔎 Search Quiz", "ar": "🔎 البحث عن سؤال"},
    "ban_user": {"en": "🚫 Ban User", "ar": "🚫 حظر مستخدم"},
    "unban_user": {"en": "✅ Unban User", "ar": "✅ رفع الحظر"},
    "backup_db": {"en": "📦 Backup DB", "ar": "📦 نسخ احتياطي"},
    "permission_error": {"en": "❌ No permission to send polls.", "ar": "❌ لا صلاحية لإرسال استبيانات."},
    "back": {"en": "🔙 Back", "ar": "🔙 رجوع"},
    "random_quiz": {"en": "🎲 Random Quiz", "ar": "🎲 سؤال عشوائي"},
}

def get_text(key, lang, **kwargs):
    """استرجاع النصوص المترجمة."""
    return TEXTS[key].get(lang, TEXTS[key]["en"]).format(**kwargs)

async def get_db():
    """إنشاء اتصال بقاعدة البيانات."""
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH)
        _db_conn.row_factory = aiosqlite.Row  # تحسين الوصول إلى البيانات
    return _db_conn

async def init_db(conn):
    """تهيئة جداول قاعدة البيانات."""
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
        "CREATE TABLE IF NOT EXISTS quizzes (quiz_id TEXT PRIMARY KEY, question TEXT, options TEXT, correct_option INTEGER, user_id INTEGER, tags TEXT, created_at INTEGER DEFAULT (strftime('%s', 'now')), answered_count INTEGER DEFAULT 0)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS default_channels (user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS banned_users (user_id INTEGER PRIMARY KEY)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY, permissions TEXT)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, admin_id INTEGER, action TEXT, target_id INTEGER, timestamp INTEGER DEFAULT (strftime('%s', 'now')))"
    )
    await conn.commit()

async def schedule_cleanup():
    """تنظيف دوري لقاعدة البيانات."""
    while True:
        await asyncio.sleep(86400)  # كل يوم
        conn = await get_db()
        await conn.execute("DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent > 0)")
        await conn.execute("DELETE FROM user_stats WHERE sent = 0")
        await conn.commit()

def parse_mcq(text):
    """تحليل نص MCQ إلى سؤال وخيارات وإجابة ووسوم."""
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
            tags = re.findall(r"#\w+", text)
            res.append((q, opts, idx, tags))
    return res

async def process_queue(chat_id, context, user_id=None, is_private=False, quiz_id=None):
    """معالجة قائمة إرسال الاستبيانات."""
    conn = await get_db()
    while send_queues[chat_id]:
        q, opts, idx, tags = send_queues[chat_id].popleft()
        try:
            poll = await context.bot.send_poll(
                chat_id,
                q,
                opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False,
            )
            await asyncio.sleep(0.5)
            msg_id = context.user_data.pop("message_to_delete", None)
            if msg_id:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            if not quiz_id:
                quiz_id = hashlib.md5((q + ':::'.join(opts)).encode()).hexdigest()
                await conn.execute(
                    "INSERT OR IGNORE INTO quizzes (quiz_id, question, options, correct_option, user_id, tags) VALUES (?, ?, ?, ?, ?, ?)",
                    (quiz_id, q, ':::'.join(opts), idx, user_id, ','.join(tags)),
                )
            else:
                await conn.execute(
                    "UPDATE quizzes SET answered_count = answered_count + 1 WHERE quiz_id = ?",
                    (quiz_id,),
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
                await conn.execute("INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?, 0)", (chat_id,))
                await conn.execute("UPDATE channel_stats SET sent = sent + 1 WHERE chat_id = ?", (chat_id,))
            await conn.commit()
        except TelegramError as e:
            lang = context.user_data.get("lang", "en")
            if "can't send polls" in str(e).lower():
                await context.bot.send_message(chat_id, get_text("permission_error", lang))
            else:
                logger.error(f"خطأ في إرسال الاستبيان: {e}")
                send_queues[chat_id].appendleft((q, opts, idx, tags))
            break

async def enqueue_mcq(msg, context, override=None, is_private=False):
    """إضافة MCQ إلى قائمة الانتظار."""
    uid = msg.from_user.id
    conn = await get_db()
    banned = await (await conn.execute("SELECT user_id FROM banned_users WHERE user_id=?", (uid,))).fetchone()
    if banned:
        await msg.reply_text("🚫 أنت محظور من استخدام البوت.")
        return False
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone()
    default_channel = row['chat_id'] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or msg.chat.id)
    lang = (msg.from_user.language_code or "en")[:2]
    context.user_data["lang"] = lang
    text = msg.text or msg.caption or ""
    blocks = [b for b in re.split(r"\n{2,}", text) if b.strip()]
    found = False
    for blk in blocks:
        for q, o, i, tags in parse_mcq(blk):
            send_queues[cid].append((q, o, i, tags))
            found = True
    if found:
        context.user_data["message_to_delete"] = msg.message_id
        asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=is_private))
    return found

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية."""
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

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة منشورات القنوات."""
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
    """معالجة أمر /start."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    context.user_data["lang"] = lang
    args = context.args
    if args and args[0].startswith("quiz_"):
        quiz_id = args[0][5:]
        conn = await get_db()
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx = row["question"], row["options"], row["correct_option"]
            opts = opts_str.split(":::")
            send_queues[update.effective_chat.id].append((q, opts, idx, []))
            asyncio.create_task(process_queue(update.effective_chat.id, context, user_id=update.effective_user.id, is_private=False, quiz_id=quiz_id))
        else:
            await update.message.reply_text(get_text("no_q", lang))
        return
    kb = [
        [InlineKeyboardButton(get_text("random_quiz", lang), callback_data="random_quiz")],
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
    """تعيين قناة افتراضية."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_channels", lang))
        return
    kb = [[InlineKeyboardButton(t, callback_data=f"set_default_{cid}")] for cid, t in rows]
    kb.append([InlineKeyboardButton(get_text("back", lang), callback_data="back_to_start")])
    await update.message.reply_text(
        "اختر قناة:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def repost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعادة نشر استبيان."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    if not context.args:
        await update.message.reply_text("❌ أدخل معرف السؤال. مثال: /repost <quiz_id>")
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
    kb.append([InlineKeyboardButton(get_text("back", lang), callback_data="back_to_start")])
    await update.message.reply_text(
        "اختر قناة لإعادة النشر:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """لوحة الإدارة."""
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
        await update.message.reply_text("❌ غير مسموح لك.")
        return
    lang = context.user_data.get("lang", "en")
    kb = [
        [InlineKeyboardButton(get_text("stats_full", lang), callback_data="admin_stats")],
        [InlineKeyboardButton(get_text("download_db", lang), callback_data="admin_download_db")],
        [InlineKeyboardButton(get_text("search_quiz", lang), callback_data="admin_search_quiz")],
        [InlineKeyboardButton(get_text("ban_user", lang), callback_data="admin_ban_user")],
        [InlineKeyboardButton(get_text("unban_user", lang), callback_data="admin_unban_user")],
        [InlineKeyboardButton(get_text("backup_db", lang), callback_data="admin_backup_db")],
    ]
    await update.message.reply_text(
        get_text("admin_panel", lang), reply_markup=InlineKeyboardMarkup(kb)
    )

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة استفسارات الأزرار."""
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = context.user_data.get("lang", "en")
    conn = await get_db()
    if cmd == "back_to_start":
        await start(update, context)
        return
    if cmd == "help":
        txt = get_text("help", lang)
    elif cmd == "new":
        txt = get_text("new", lang)
    elif cmd == "stats":
        r = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (uid,))).fetchone()
        s = await (await conn.execute("SELECT SUM(sent) FROM channel_stats")).fetchone()
        txt = get_text("stats", lang, pr=r["sent"] if r else 0, ch=s[0] if s else 0)
    elif cmd == "channels":
        rows = await get_known_channels()
        if not rows:
            txt = get_text("no_channels", lang)
        else:
            channels_list = "\n".join(f"- {t}: {cid}" for cid, t in rows)
            txt = get_text("channels_list", lang, channels=channels_list)
        kb = [[InlineKeyboardButton(get_text("back", lang), callback_data="back_to_start")]]
        await update.callback_query.edit_message_text(txt, reply_markup=InlineKeyboardMarkup(kb))
        return
    elif cmd == "publish_channel":
        rows = await get_known_channels()
        if not rows:
            await update.callback_query.edit_message_text(get_text("no_channels", lang))
            return
        kb = [[InlineKeyboardButton(t, callback_data=f"choose_{cid}")] for cid, t in rows]
        kb.append([InlineKeyboardButton(get_text("back", lang), callback_data="back_to_start")])
        await update.callback_query.edit_message_text(
            "اختر قناة:", reply_markup=InlineKeyboardMarkup(kb)
        )
        return
    elif cmd.startswith("choose_"):
        cid = int(cmd.split("_")[1])
        row = await (await conn.execute("SELECT title FROM known_channels WHERE chat_id=?", (cid,))).fetchone()
        if row:
            context.chat_data["target_channel"] = cid
            txt = f"✅ تم اختيار القناة: {row['title']}.\n" + get_text("private_channel_warning", lang)
        else:
            txt = "❌ القناة غير موجودة"
    elif cmd.startswith("set_default_"):
        cid = int(cmd.split("_")[2])
        row = await (await conn.execute("SELECT title FROM known_channels WHERE chat_id=?", (cid,))).fetchone()
        if row:
            await conn.execute(
                "INSERT OR REPLACE INTO default_channels (user_id, chat_id, title) VALUES (?, ?, ?)",
                (uid, cid, row['title']),
            )
            await conn.commit()
            txt = get_text("set_channel_success", lang, title=row['title'])
        else:
            txt = get_text("no_channels", lang)
    elif cmd == "random_quiz":
        row = await (await conn.execute("SELECT quiz_id FROM quizzes ORDER BY RANDOM() LIMIT 1")).fetchone()
        if row:
            quiz_id = row["quiz_id"]
            await context.bot.send_message(update.effective_chat.id, f"/start quiz_{quiz_id}")
        else:
            await context.bot.send_message(update.effective_chat.id, get_text("no_q", lang))
        return
    elif cmd.startswith("repost_"):
        quiz_id = cmd.split("_")[1]
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            rows = await get_known_channels()
            if not rows:
                await update.callback_query.edit_message_text(get_text("no_channels", lang))
                return
            kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
            kb.append([InlineKeyboardButton(get_text("back", lang), callback_data="back_to_start")])
            await update.callback_query.edit_message_text(
                "اختر قناة لإعادة النشر:", reply_markup=InlineKeyboardMarkup(kb)
            )
            return
        else:
            txt = get_text("no_q", lang)
    elif cmd.startswith("repost_to_"):
        quiz_id, cid = cmd.split("_")[2:]
        cid = int(cid)
        row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx = row["question"], row["options"], row["correct_option"]
            opts = opts_str.split(":::")
            send_queues[cid].append((q, opts, idx, []))
            asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=False, quiz_id=quiz_id))
            txt = get_text("quiz_sent", lang)
        else:
            txt = get_text("no_q", lang)
    elif cmd.startswith("admin_"):
        if uid not in ADMIN_IDS:
            txt = "❌ غير مسموح لك."
        elif cmd == "admin_stats":
            users = await (await conn.execute("SELECT COUNT(*) FROM user_stats")).fetchone()
            channels = await (await conn.execute("SELECT COUNT(*) FROM channel_stats")).fetchone()
            banned = await (await conn.execute("SELECT COUNT(*) FROM banned_users")).fetchone()
            txt = get_text("stats_full", lang, users=users[0], channels=channels[0], banned=banned[0])
        elif cmd == "admin_download_db":
            await conn.commit()
            await conn.close()
            await context.bot.send_document(chat_id=update.effective_chat.id, document=open(DB_PATH, 'rb'), filename="stats.db")
            global _db_conn
            _db_conn = await aiosqlite.connect(DB_PATH)
            _db_conn.row_factory = aiosqlite.Row
            return
        elif cmd == "admin_search_quiz":
            await update.callback_query.edit_message_text("🔎 أرسل معرف السؤال أو وسم (مثل #math).")
            context.user_data["admin_action"] = "search_quiz"
            return
        elif cmd == "admin_ban_user":
            await update.callback_query.edit_message_text("🚫 أرسل معرف المستخدم لحظره.")
            context.user_data["admin_action"] = "ban_user"
            return
        elif cmd == "admin_unban_user":
            await update.callback_query.edit_message_text("✅ أرسل معرف المستخدم لرفع الحظر.")
            context.user_data["admin_action"] = "unban_user"
            return
        elif cmd == "admin_backup_db":
            await update.callback_query.edit_message_text("📦 جارٍ إنشاء نسخة احتياطية...")
            context.user_data["admin_action"] = "backup_db"
            return
    await update.callback_query.edit_message_text(txt)

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الاستعلامات المضمنة."""
    q = update.inline_query.query
    if not q:
        return
    conn = await get_db()
    results = []
    if q.startswith("#"):
        tag = q[1:]
        rows = await (await conn.execute("SELECT quiz_id, question FROM quizzes WHERE tags LIKE ? LIMIT 5", (f"%{tag}%",))).fetchall()
    else:
        rows = await (await conn.execute("SELECT quiz_id, question FROM quizzes WHERE question LIKE ? LIMIT 5", (f"%{q}%",))).fetchall()
    for row in rows:
        quiz_id, question = row["quiz_id"], row["question"]
        results.append(
            InlineQueryResultArticle(
                id=quiz_id,
                title=question[:50] + "..." if len(question) > 100 else question,
                input_message_content=InputTextMessageContent(f"/start quiz_{quiz_id}"),
                description="اضغط لإرسال السؤال",
            )
        )
    if not results:
        results.append(
            InlineQueryResultArticle(
                id="1",
                title="تحويل إلى MCQ",
                input_message_content=InputTextMessageContent(q),
            )
        )
    await update.inline_query.answer(results)

@alru_cache(maxsize=1)
async def get_known_channels():
    """استرجاع القنوات المعروفة مع التخزين المؤقت."""
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    return [(row['chat_id'], row['title']) for row in rows]

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض قائمة القنوات."""
    lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
    rows = await get_known_channels()
    if not rows:
        txt = get_text("no_channels", lang)
    else:
        channels_list = "\n".join(f"- {t}: {cid}" for cid, t in rows)
        txt = get_text("channels_list", lang, channels=channels_list)
    kb = [[InlineKeyboardButton(get_text("back", lang), callback_data="back_to_start")]]
    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))

async def admin_action_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الإجراءات الإدارية."""
    msg = update.message
    if not msg or not msg.text:
        return
    uid = msg.from_user.id
    if uid not in ADMIN_IDS:
        return
    action = context.user_data.get("admin_action")
    if not action:
        return
    conn = await get_db()
    lang = context.user_data.get("lang", "en")
    if action == "search_quiz":
        search_term = msg.text.strip()
        if search_term.startswith("#"):
            tag = search_term[1:]
            rows = await (await conn.execute("SELECT quiz_id, question, answered_count FROM quizzes WHERE tags LIKE ?", (f"%{tag}%",))).fetchall()
            if rows:
                txt = "\n".join(f"ID: {row['quiz_id']}, سؤال: {row['question'][:50]}..., إجابات: {row['answered_count']}" for row in rows)
            else:
                txt = get_text("no_q", lang)
        else:
            row = await (await conn.execute("SELECT question, options, correct_option, tags, answered_count FROM quizzes WHERE quiz_id=?", (search_term,))).fetchone()
            if row:
                q, opts_str, idx, tags, answered_count = row["question"], row["options"], row["correct_option"], row["tags"], row["answered_count"]
                opts = opts_str.split(":::")
                txt = f"السؤال: {q}\nالخيارات:\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(opts)) + f"\nالإجابة: {opts[idx]}\nالوسوم: {tags}\nالإجابات: {answered_count}"
            else:
                txt = get_text("no_q", lang)
    elif action in ["ban_user", "unban_user"]:
        try:
            user_id = int(msg.text.strip())
            if action == "ban_user":
                await conn.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
                await conn.execute("INSERT INTO audit_log (admin_id, action, target_id) VALUES (?, ?, ?)", (uid, "ban", user_id))
                txt = f"🚫 تم حظر {user_id}"
            else:
                await conn.execute("DELETE FROM banned_users WHERE user_id=?", (user_id,))
                await conn.execute("INSERT INTO audit_log (admin_id, action, target_id) VALUES (?, ?, ?)", (uid, "unban", user_id))
                txt = f"✅ تم رفع الحظر عن {user_id}"
            await conn.commit()
        except ValueError:
            txt = "❌ معرف غير صحيح."
    else:
        txt = "⚠️ غير مدعوم"
    await msg.reply_text(txt)
    context.user_data.pop("admin_action", None)

def main():
    """تشغيل البوت."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("❌ يجب تعيين TELEGRAM_BOT_TOKEN.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("setchannel", set_channel))
    app.add_handler(CommandHandler("repost", repost))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_IDS), admin_action_handler))
    async def startup(application):
        conn = await get_db()
        await init_db(conn)
        asyncio.create_task(schedule_cleanup())
    app.post_init = startup
    async def shutdown(application):
        global _db_conn
        if _db_conn:
            await _db_conn.close()
            _db_conn = None
    app.post_shutdown = shutdown
    logger.info("✅ البوت جاهز...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main() 
