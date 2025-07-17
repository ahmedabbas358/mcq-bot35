import os
import re
import logging
import asyncio
import time
import hashlib
import threading
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
_active_tasks = {}  # لتتبع المهام النشطة
bot_lock = threading.Lock()  # لمنع التشغيل المزدوج

# دعم الأرقام والحروف
ARABIC_DIGITS = {"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}
ARABIC_DIGITS.update({str(i): str(i) for i in range(10)})
EN_LETTERS = {chr(ord("A") + i): i for i in range(10)}
EN_LETTERS.update({chr(ord("a") + i): i for i in range(10)})
AR_LETTERS = {"أ": 0, "ب": 1, "ج": 2, "د": 3, "هـ": 4, "و": 5, "ز": 6, "ح": 7, "ط": 8, "ي": 9}

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
    "bot_restarted": {
        "en": "🔵 Bot restarted successfully!\nRestart time: {restart_time}",
        "ar": "🔵 تم إعادة تشغيل البوت بنجاح!\nوقت إعادة التشغيل: {restart_time}"
    }
}

def get_text(key, lang, **kwargs):
    """Retrieve translated text with fallback to English."""
    return TEXTS[key].get(lang, TEXTS[key]["en"]).format(**kwargs)

async def get_db():
    """Create a new database connection for each operation."""
    return await aiosqlite.connect(DB_PATH)

async def init_db():
    """Initialize database tables."""
    async with await get_db() as conn:
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

async def cleanup_db():
    """Clean up unused database entries."""
    try:
        async with await get_db() as conn:
            await conn.execute(
                "DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent > 0)"
            )
            await conn.execute("DELETE FROM user_stats WHERE sent = 0")
            await conn.commit()
            logger.info("✅ Database cleanup completed")
    except Exception as e:
        logger.error(f"Database cleanup error: {e}")

def parse_mcq(text):
    """Parse MCQ text into question, options, and correct answer index."""
    patterns = [
        # النمط الإنجليزي المحسن
        re.compile(
            r"(?:Q|Question|سؤال)[.:)\s]*(?P<q>.+?)\s*"
            r"(?P<opts>(?:[A-Za-z][).\s]+\s*.+?(?:\n|$)){2,10}"
            r"(?:Answer|الإجابة|Correct\s*Answer)[:\s]*(?P<ans>[A-Za-z])",
            re.IGNORECASE | re.DOTALL
        ),
        # النمط العربي المحسن
        re.compile(
            r"(?:س|سؤال)[.:)\s]*(?P<q>.+?)\s*"
            r"(?P<opts>(?:[أ-ي][).\s]+\s*.+?(?:\n|$)){2,10}"
            r"(?:الإجابة|الجواب|الإجابة\s*الصحيحة)[:\s]*(?P<ans>[أ-ي])",
            re.DOTALL
        ),
        # نمط الأرقام
        re.compile(
            r"(?:Q|س)[.:)\s]*(?P<q>.+?)\s*"
            r"(?P<opts>(?:\d+[).\s]+\s*.+?(?:\n|$)){2,10}"
            r"(?:Answer|الإجابة)[:\s]*(?P<ans>\d+)",
            re.IGNORECASE | re.DOTALL
        )
    ]
    
    results = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            question = match.group("q").strip()
            options_text = match.group("opts")
            
            # استخراج الخيارات مع تحسين التعامل مع المسافات
            options = [
                opt.strip() 
                for opt in re.findall(
                    r"[A-Za-zأ-ي0-9][).\s]+\s*(.+?)(?=\n|$)", 
                    options_text, 
                    re.DOTALL
                )
            ]
            
            answer = match.group("ans").strip().upper()
            
            # تحويل الإجابة إلى رقم مؤشر
            if answer.isdigit():
                idx = int(answer) - 1
            elif answer in EN_LETTERS:
                idx = EN_LETTERS[answer]
            elif answer in AR_LETTERS:
                idx = AR_LETTERS[answer]
            else:
                continue
                
            if 0 <= idx < len(options):
                results.append((question, options, idx))
    
    return results

async def send_quiz(chat_id, context, question, options, correct_idx, quiz_id=None, user_id=None, is_private=False):
    """Send a quiz and handle database operations."""
    try:
        if not quiz_id:
            quiz_id = hashlib.md5(
                (question + ':::' + ':::'.join(options)).encode()).hexdigest()
        
        # إرسال الاستطلاع
        poll = await context.bot.send_poll(
            chat_id=chat_id,
            question=question,
            options=options,
            type=Poll.QUIZ,
            correct_option_id=correct_idx,
            is_anonymous=False,
            explanation="تم إنشاء هذا السؤال بواسطة بوت MCQ"
        )
        
        # حفظ في قاعدة البيانات
        async with await get_db() as conn:
            await conn.execute(
                """INSERT OR REPLACE INTO quizzes 
                (quiz_id, question, options, correct_option, user_id)
                VALUES (?, ?, ?, ?, ?)""",
                (quiz_id, question, ':::'.join(options), correct_idx, user_id)
            )
            await conn.commit()
        
        # تحديث الإحصائيات
        table = "user_stats" if is_private else "channel_stats"
        async with await get_db() as conn:
            await conn.execute(
                f"""INSERT OR IGNORE INTO {table} 
                ({'user_id' if is_private else 'chat_id'}, sent) 
                VALUES (?, 0)""",
                (user_id if is_private else chat_id,)
            )
            await conn.execute(
                f"UPDATE {table} SET sent = sent + 1 WHERE "
                f"{'user_id' if is_private else 'chat_id'} = ?",
                (user_id if is_private else chat_id,)
            )
            await conn.commit()
        
        # إعداد أزرار المشاركة
        lang = context.user_data.get("lang", "en")
        bot_username = (await context.bot.get_me()).username
        share_url = f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
        
        keyboard = [
            [InlineKeyboardButton(
                get_text("share_quiz", lang), 
                url=share_url
            )],
            [InlineKeyboardButton(
                get_text("repost_quiz", lang),
                callback_data=f"repost_{quiz_id}"
            )]
        ]
        
        # إرسال رسالة التأكيد
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_text("quiz_sent", lang),
            reply_markup=InlineKeyboardMarkup(keyboard),
            reply_to_message_id=poll.message_id
        )
        
        return True
    except Exception as e:
        logger.error(f"Error sending quiz: {str(e)}")
        return False

async def process_queue(chat_id, context, user_id=None, is_private=False):
    """Process the send queue for a given chat."""
    task_key = f'queue_{chat_id}'
    if task_key in _active_tasks and not _active_tasks[task_key].done():
        return

    async def _process():
        while send_queues[chat_id]:
            q, opts, idx, quiz_id, msg_id = send_queues[chat_id].popleft()
            
            try:
                success = await send_quiz(
                    chat_id, 
                    context, 
                    q, 
                    opts, 
                    idx, 
                    quiz_id=quiz_id,
                    user_id=user_id,
                    is_private=is_private
                )
                
                if success and msg_id:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    except Exception as e:
                        logger.warning(f"Failed to delete message: {e}")
                
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Queue processing error: {e}")
                send_queues[chat_id].appendleft((q, opts, idx, quiz_id, msg_id))
                await asyncio.sleep(5)
                break

    _active_tasks[task_key] = asyncio.create_task(_process())

async def enqueue_mcq(msg, context, override=None, is_private=False):
    """Enqueue an MCQ for processing."""
    try:
        uid = msg.from_user.id
        lang = (msg.from_user.language_code or "en")[:2]
        context.user_data["lang"] = lang
        
        # الحصول على القناة الافتراضية
        async with await get_db() as conn:
            row = await conn.execute_fetchone(
                "SELECT chat_id FROM default_channels WHERE user_id=?",
                (uid,))
        default_channel = row[0] if row else None
        
        cid = override or context.chat_data.get("target_channel", default_channel or msg.chat.id)
        
        # التحقق من حجم الطابور
        if len(send_queues[cid]) >= MAX_QUEUE_SIZE:
            await context.bot.send_message(
                msg.chat.id, 
                get_text("queue_full", lang)
            )
            return False
        
        text = msg.text or msg.caption or ""
        
        # تقسيم النص إلى كتل (أسئلة منفصلة)
        blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
        found = False
        
        for block in blocks:
            # معالجة كل كتلة كمستند MCQ منفصل
            for question, options, correct_idx in parse_mcq(block):
                quiz_id = hashlib.md5(
                    (question + ':::' + ':::'.join(options)).encode()).hexdigest()
                
                send_queues[cid].append(
                    (question, options, correct_idx, quiz_id, msg.message_id)
                )
                found = True
        
        if found:
            await process_queue(cid, context, 
                              user_id=uid, 
                              is_private=is_private)
            return True
        
        if not found and is_private:
            await context.bot.send_message(
                msg.chat.id, 
                get_text("no_q", lang)
            )
        return found
        
    except Exception as e:
        logger.error(f"Error in enqueue_mcq: {e}")
        return False

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text messages."""
    try:
        msg = update.message
        if not msg or not (msg.text or msg.caption):
            return
        
        # منع التكرار السريع
        uid = msg.from_user.id
        current_time = time.time()
        if current_time - last_sent_time.get(uid, 0) < 3:
            return
        last_sent_time[uid] = current_time

        chat_type = msg.chat.type
        if chat_type == ChatType.PRIVATE:
            await enqueue_mcq(msg, context, is_private=True)
            return

        # للمجموعات: التأكد من أن الرسالة موجهة للبوت
        content = (msg.text or msg.caption or "").lower()
        bot_username = (await context.bot.get_me()).username.lower()
        
        if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            is_reply_to_bot = (
                msg.reply_to_message and 
                msg.reply_to_message.from_user.id == context.bot.id
            )
            is_mention = f"@{bot_username}" in content
            
            if is_reply_to_bot or is_mention:
                await enqueue_mcq(msg, context, is_private=False)
                
    except Exception as e:
        logger.error(f"Error in handle_text: {e}")
        lang = (update.effective_user.language_code or "en")[:2]
        await update.message.reply_text(get_text("invalid_format", lang))

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle channel posts."""
    try:
        post = update.channel_post
        if not post:
            return
        
        async with await get_db() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)",
                (post.chat.id, post.chat.title or ""),
            )
            await conn.commit()
        
        found = await enqueue_mcq(post, context, is_private=False)
        if not found:
            lang = (post.from_user.language_code or "en")[:2]
            await context.bot.send_message(post.chat.id, get_text("no_q", lang))
    except Exception as e:
        logger.error(f"Error in handle_channel_post: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /start command."""
    try:
        lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
        context.user_data["lang"] = lang
        args = context.args
        
        if args and args[0].startswith("quiz_"):
            quiz_id = args[0][5:]
            async with await get_db() as conn:
                row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
            
            if row:
                q, opts_str, idx = row
                opts = opts_str.split(":::")
                quiz_hash = hashlib.md5((q + ':::'.join(opts)).encode()).hexdigest()
                send_queues[update.effective_chat.id].append((q, opts, idx, quiz_id, None))
                await process_queue(update.effective_chat.id, context, user_id=update.effective_user.id, is_private=False)
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
            get_text("start", lang), 
            reply_markup=InlineKeyboardMarkup(kb)
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set a default channel for posting quizzes."""
    try:
        lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
        async with await get_db() as conn:
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        
        if not rows:
            await update.message.reply_text(get_text("no_channels", lang))
            return
        
        kb = [[InlineKeyboardButton(t, callback_data=f"set_default_{cid}")] for cid, t in rows]
        await update.message.reply_text(
            "اختر قناة لتعيينها كافتراضية:", 
            reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Error in set_channel: {e}")

async def repost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Repost a quiz by ID."""
    try:
        lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
        if not context.args:
            await update.message.reply_text("❌ يرجى تقديم معرف الاختبار. مثال: /repost <quiz_id>")
            return
        
        quiz_id = context.args[0]
        async with await get_db() as conn:
            row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        
        if not row:
            await update.message.reply_text(get_text("no_q", lang))
            return
        
        if not rows:
            await update.message.reply_text(get_text("no_channels", lang))
            return
        
        q, opts_str, idx = row
        opts = opts_str.split(":::")
        kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
        await update.message.reply_text(
            "اختر مكانًا لإعادة النشر:", 
            reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Error in repost: {e}")

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle callback queries from inline keyboard."""
    try:
        cmd = update.callback_query.data
        uid = update.effective_user.id
        lang = context.user_data.get("lang", "en")
        txt = "⚠️ غير مدعوم"
        
        if cmd == "help":
            txt = get_text("help", lang)
        elif cmd == "new":
            txt = get_text("new", lang)
        elif cmd == "stats":
            async with await get_db() as conn:
                r = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (uid,))).fetchone()
                s = await (await conn.execute("SELECT SUM(sent) FROM channel_stats")).fetchone()
            txt = get_text("stats", lang, pr=r[0] if r else 0, ch=s[0] if s else 0)
        elif cmd == "channels":
            async with await get_db() as conn:
                rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if not rows:
                txt = get_text("no_channels", lang)
            else:
                channels_list = "\n".join(f"- {t}: {cid}" for cid, t in rows)
                txt = get_text("channels_list", lang, channels=channels_list)
        elif cmd == "publish_channel":
            async with await get_db() as conn:
                rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if not rows:
                await update.callback_query.edit_message_text(get_text("no_channels", lang))
                return
            kb = [[InlineKeyboardButton(t, callback_data=f"choose_{cid}")] for cid, t in rows]
            await update.callback_query.edit_message_text(
                "اختر قناة:", reply_markup=InlineKeyboardMarkup(kb))
            return
        elif cmd.startswith("choose_"):
            cid = int(cmd.split("_")[1])
            async with await get_db() as conn:
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
            async with await get_db() as conn:
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
            async with await get_db() as conn:
                row = await (await conn.execute("SELECT question, options, correct_option FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
                rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if row and rows:
                kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
                await update.callback_query.edit_message_text(
                    "اختر مكانًا لإعادة النشر:", reply_markup=InlineKeyboardMarkup(kb))
                return
            else:
                txt = get_text("no_q", lang)
        elif cmd.startswith("repost_to_"):
            _, __, quiz_id, cid = cmd.split("_", 3)
            cid = int(cid)
            async with await get_db() as conn:
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
    except Exception as e:
        logger.error(f"Error in callback handler: {e}")

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline queries."""
    try:
        q = update.inline_query.query
        if not q:
            return
        result = InlineQueryResultArticle(
            id="1",
            title="تحويل إلى MCQ",
            input_message_content=InputTextMessageContent(q),
        )
        await update.inline_query.answer([result])
    except Exception as e:
        logger.error(f"Error in inline query: {e}")

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the /channels command."""
    try:
        lang = (getattr(update.effective_user, "language_code", "en") or "en")[:2]
        async with await get_db() as conn:
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        if not rows:
            txt = get_text("no_channels", lang)
        else:
            channels_list = "\n".join(f"- {t}: {cid}" for cid, t in rows)
            txt = get_text("channels_list", lang, channels=channels_list)
        await update.message.reply_text(txt)
    except Exception as e:
        logger.error(f"Error in channels command: {e}")

async def periodic_cleanup(context: ContextTypes.DEFAULT_TYPE):
    """Periodic database cleanup task."""
    try:
        while True:
            await cleanup_db()
            await asyncio.sleep(86400)  # يوم واحد
    except asyncio.CancelledError:
        logger.info("Cleanup task cancelled")
    except Exception as e:
        logger.error(f"Periodic cleanup error: {e}")
        await asyncio.sleep(3600)  # انتظر ساعة قبل إعادة المحاولة

async def health_check(context: ContextTypes.DEFAULT_TYPE):
    """Send health status message on startup."""
    try:
        me = await context.bot.get_me()
        logger.info(f"✅ Bot is running: {me.username}")
        
        # Send message to developer if specified
        developer_id = os.getenv("DEVELOPER_ID")
        if developer_id:
            current_time = time.strftime("%Y-%m-%d %H:%M:%S")
            lang = "ar"  # Default to Arabic
            await context.bot.send_message(
                chat_id=developer_id,
                text=get_text("health_check", lang, start_time=current_time)
            )
    except Exception as e:
        logger.error(f"Health check failed: {e}")

async def init_app(application: Application):
    """Initialize the application on startup."""
    if getattr(application, "_initialized", False):
        return
    
    try:
        await init_db()
        
        # Delete any existing webhook
        async with application:
            await application.bot.delete_webhook(drop_pending_updates=True)
        
        # Add all handlers
        application.add_handler(CommandHandler(["start", "help"], start))
        application.add_handler(CommandHandler("channels", channels_command))
        application.add_handler(CommandHandler("setchannel", set_channel))
        application.add_handler(CommandHandler("repost", repost))
        application.add_handler(CallbackQueryHandler(callback_query_handler))
        application.add_handler(InlineQueryHandler(inline_query))
        application.add_handler(MessageHandler(
            filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption),
            handle_channel_post,
        ))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

        # Start background tasks
        if 'cleanup' not in _active_tasks or _active_tasks['cleanup'].done():
            _active_tasks['cleanup'] = asyncio.create_task(periodic_cleanup(application))
        
        await health_check(application)
        application._initialized = True
        logger.info("✅ Application fully initialized")
    except Exception as e:
        logger.error(f"Application initialization failed: {e}")
        raise

async def cleanup_on_shutdown(application: Application):
    """Cleanup tasks on shutdown."""
    logger.info("Shutting down...")
    for task_name, task in _active_tasks.items():
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"Task {task_name} cancelled successfully")
            except Exception as e:
                logger.error(f"Error cancelling task {task_name}: {e}")

def main():
    """Main entry point for the bot."""
    if not bot_lock.acquire(blocking=False):
        logger.error("⚠️ البوت يعمل بالفعل! لا يمكن تشغيل نسخة ثانية.")
        return

    application = None

    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("❌ Bot token not found. Set TELEGRAM_BOT_TOKEN environment variable.")
        
        # Create application with post_init and shutdown handlers
        application = (
            Application.builder()
            .token(token)
            .post_init(init_app)
            .post_shutdown(cleanup_on_shutdown)
            .concurrent_updates(True)
            .build()
        )
        
        # Start the bot AFTER handlers and setup are complete
        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
            close_loop=False,
        )

    except Exception as e:
        logger.critical(f"Fatal error: {e}")
    finally:
        bot_lock.release()
        if application:
            asyncio.get_event_loop().run_until_complete(cleanup_on_shutdown(application))

if __name__ == "__main__":
    main()
