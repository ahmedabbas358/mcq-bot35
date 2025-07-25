import os
import re
import logging
import asyncio
import time
import hashlib
import signal
import contextlib
import psutil
from collections import defaultdict
from typing import Dict, Deque, Tuple, List, Optional

import aiosqlite
import telegram
from telegram import (
    Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    InlineQueryHandler, filters, ContextTypes
)
from telegram.constants import ChatType

# ------------------------------------------------------------------
# 1. Logging (Railway expects stdout)
# ------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# 2. Environment & Config
# ------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", "stats.db")
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "50"))
SEND_INTERVAL = float(os.getenv("SEND_INTERVAL", "0.75"))         # seconds between polls
MAX_CONCURRENT_SEND = int(os.getenv("MAX_CONCURRENT_SEND", "5"))  # max parallel polls
MAX_QUESTION_LENGTH = 300
MAX_OPTION_LENGTH = 100

# ------------------------------------------------------------------
# 3. Translation, constants
# ------------------------------------------------------------------
ARABIC_DIGITS = {**{str(i): str(i) for i in range(10)},
                 **{"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4",
                    "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}}

# أضف هذا القاموس مباشرة بعد ARABIC_DIGITS
ARABIC_LETTERS = {
    'أ': 'A', 'ا': 'A', 'إ': 'A', 'آ': 'A', 'ب': 'B', 'ت': 'C', 'ث': 'D',
    'ج': 'E', 'ح': 'F', 'خ': 'G', 'د': 'H', 'ذ': 'I', 'ر': 'J', 'ز': 'K',
    'س': 'L', 'ش': 'M', 'ص': 'N', 'ض': 'O', 'ط': 'P', 'ظ': 'Q', 'ع': 'R',
    'غ': 'S', 'ف': 'T', 'ق': 'U', 'ك': 'V', 'ل': 'W', 'م': 'X', 'ن': 'Y',
    'ه': 'Z', 'ة': 'Z', 'و': 'W', 'ي': 'Y', 'ئ': 'Y', 'ء': ''
}

QUESTION_PREFIXES = ["Q", "Question", "س", "سؤال"]
ANSWER_KEYWORDS = ["Answer", "Ans", "Correct Answer", "الإجابة", "الجواب", "الإجابة الصحيحة"]

TEXTS = {
    "start": {"en": "🤖 Hi! Choose an option:", "ar": "🤖 أهلاً! اختر من القائمة:"},
    "help": {
        "en": "🆘 Usage:\n- Send MCQ in private.\n- To publish in a channel: use 🔄 or /setchannel.\n- In groups: reply or mention @bot.",
        "ar": "🆘 كيفية الاستخدام:\n- في الخاص: أرسل السؤال بصيغة Q:/س:.\n- للنشر في قناة: استخدم 🔄 أو /setchannel.\n- في المجموعات: رُدّ على البوت أو اذكر @البوت."
    },
    "new": {"en": "📩 Send your MCQ now!", "ar": "📩 أرسل سؤال MCQ الآن!"},
    "stats": {
        "en": "📊 Private: {pr} questions.\n🏷️ Channel: {ch} posts.",
        "ar": "📊 في الخاص: {pr} سؤال.\n🏷️ في القنوات: {ch} منشور."
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
        "ar": "⚠️ تأكد أن البوت مشرف في القناة الخاصة."
    },
    "set_channel_success": {"en": "✅ Default channel set: {title}", "ar": "✅ تم تعيين القناة الافتراضية: {title}"},
    "no_channel_selected": {"en": "❌ No channel selected.", "ar": "❌ لم يتم اختيار قناة."},
    "health_check": {
        "en": "🟢 Bot is running!\nStart time: {start_time}",
        "ar": "🟢 البوت يعمل الآن!\nوقت البدء: {start_time}"
    },
}

def get_text(key: str, lang: str, **kwargs) -> str:
    return TEXTS[key].get(lang, TEXTS[key]["en"]).format(**kwargs)

# ------------------------------------------------------------------
# 4. Memory monitoring
# ------------------------------------------------------------------
def log_memory_usage():
    mem = psutil.Process().memory_info().rss / (1024 * 1024)
    logger.info(f"Memory usage: {mem:.2f} MB")

# ------------------------------------------------------------------
# 5. Async-safe database singleton
# ------------------------------------------------------------------
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

# ------------------------------------------------------------------
# 6. Queue & Rate-limiting
# ------------------------------------------------------------------
SendItem = Tuple[str, List[str], int, str, Optional[int]]
send_queues: Dict[int, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=MAX_QUEUE_SIZE))
semaphores: Dict[int, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(MAX_CONCURRENT_SEND))

# ------------------------------------------------------------------
# 7. Validation function
# ------------------------------------------------------------------
def validate_mcq(q: str, options: List[str]) -> bool:
    if len(q) > MAX_QUESTION_LENGTH:
        return False
    if len(options) < 2 or len(options) > 10:
        return False
    if any(len(opt) > MAX_OPTION_LENGTH for opt in options):
        return False
    return True

# ------------------------------------------------------------------
# 8. Parsing
# ------------------------------------------------------------------
# احذف الدالتين القديمتين parse_single_mcq و parse_mcq تماماً
# واستبدلهما بالدوال الجديدة التالية:

def parse_single_mcq(block: str) -> Optional[Tuple[str, List[str], int]]:
    block = re.sub(r'[\u200b\u200c\ufeff]', '', block)  # إزالة أحرف غير مرئية
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if len(lines) > 20:  # حماية من النصوص الطويلة
        return None
        
    question, options, answer_line, answer_label = None, [], None, None
    # أنماط الأسئلة الديناميكية
    question_prefixes = QUESTION_PREFIXES + ["MCQ", "Multiple Choice", "اختبار", "اختر", "أسئلة", "Questions", "السؤال"]
    # أنماط الخيارات
    option_patterns = [
        r'^\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])\s*[).:\-]\s*(.+)',  # أ) ... | 1. ...
        r'^\s*[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]\s*(.+)',  # (أ) ... | [A] ...
        r'^\s*[\u25cb\u25cf\u25a0\u2022\u00d8\*]\s*([a-zأ-ي0-9])\s*[:.]?\s*(.+)',  # ○ أ: ... | ● ب ...
        r'^\s*[\u2794\u27a4\u2192]\s*([a-zأ-ي0-9])\s*[:.]\s*(.+)',  # ➔ أ: ... | → ب ...
        r'^\s*([a-zأ-ي0-9])\s*[\u2013\u2014]\s*(.+)',  # أ - ... | ب — ...
        r'^\s*\b(?:option|اختيار)\s*([a-zأ-ي0-9])\s*[:.]\s*(.+)'  # Option A: ... | اختيار أ: ...
    ]
    # أنماط الإجابات
    answer_keywords = ANSWER_KEYWORDS + ["Correct", "Solution", "Key", "مفتاح", "صحيح", "صح", "الحل", "الصحيحة", "الجواب هو", "الإجابة الصحيحة هي"]
    
    # معالجة كل سطر
    for i, line in enumerate(lines):
        # التعرف على السؤال: إذا لم يتم تحديده بعد
        if question is None:
            for prefix in question_prefixes:
                if line.lower().startswith(prefix.lower()):
                    question = re.sub(f'^{re.escape(prefix)}\\s*[:.\\-]?\\s*', '', line, flags=re.I).strip()
                    break
            # إذا عثرنا على سؤال، نتخطى بقية المعالجة لهذا السطر
            if question is not None:
                continue
        
        # محاولة التعرف على الخيارات
        option_found = False
        for pattern in option_patterns:
            m = re.match(pattern, line, re.I | re.U)
            if m:
                label, text = m.groups()
                # تطبيع التسمية: تحويل الأرقام العربية وإزالة التشكيل
                label = ''.join(ARABIC_DIGITS.get(c, c) for c in label).upper()
                # تحويل الأحرف العربية إلى لاتينية باستخدام ARABIC_LETTERS
                label = ''.join(ARABIC_LETTERS.get(c, c) for c in label).strip()
                if label:
                    options.append((label, text.strip()))
                    option_found = True
                    break
        if option_found:
            continue
        
        # التعرف على الإجابة: إذا لم يتم تحديدها بعد
        if answer_line is None:
            for kw in answer_keywords:
                if kw.lower() in line.lower():
                    answer_line = line
                    # استخراج التسمية من سطر الإجابة
                    # تجربة أنماط متعددة
                    patterns = [
                        r'[:：]\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])$',  # :أ
                        r'is\s+([a-zأ-ي0-9])',  # is A
                        r'هي\s+([a-zأ-ي0-9])',  # هي أ
                        r'[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]$',  # (A)
                        r'\b(?:correct|صح|صحيح)\s*[:\-]\s*([a-zأ-ي0-9])',  # Correct: A | صح- أ
                        r'[\u2714\u2705]\s*([a-zأ-ي0-9])'  # ✔ A | ✅ ب
                    ]
                    for pattern in patterns:
                        m = re.search(pattern, line, re.I | re.U)
                        if m:
                            answer_label = m.group(1)
                            # تطبيع التسمية
                            answer_label = ''.join(ARABIC_DIGITS.get(c, c) for c in answer_label).upper()
                            answer_label = ''.join(ARABIC_LETTERS.get(c, c) for c in answer_label).strip()
                            break
                    break
    
    # إذا لم نعثر على سؤال، نأخذ أول سطر غير فارغ
    if question is None and lines:
        question = lines[0]
        # إذا كانت هناك خيارات، قد نكون أخذنا سطر الخيار الأول كسؤال
        if options and len(options) > 0 and lines[0].startswith(options[0][0]):
            # إذا كان السطر الأول يطابق أول خيار، فلا نأخذه كسؤال
            question = None
    
    # التحقق من وجود المكونات الأساسية
    if not question or not options or not answer_label:
        return None
    
    # إنشاء خريطة العلامات إلى الفهرس
    label_to_idx = {}
    for idx, (label, text) in enumerate(options):
        # تنظيف العلامة من أي رموز غير مرغوبة
        clean_label = re.sub(r'[^A-Z0-9]', '', label)
        label_to_idx[clean_label] = idx
        # إذا كانت العلامة رقمية، ننشئ مرادفًا بحرف
        if clean_label.isdigit() and 1 <= int(clean_label) <= 26:
            letter = chr(64 + int(clean_label))
            label_to_idx[letter] = idx
    
    # البحث عن الإجابة في الخريطة
    clean_answer = re.sub(r'[^A-Z0-9]', '', answer_label)
    if clean_answer in label_to_idx:
        correct_index = label_to_idx[clean_answer]
        return question, [text for _, text in options], correct_index
    
    # محاولة ثانية: إذا لم نجد، نبحث في القاموس النصي
    text_answers = {
        "الأول": "A", "أول": "A", "الألف": "A", "أ": "A", "1": "A",
        "الثاني": "B", "ثاني": "B", "الباء": "B", "ب": "B", "2": "B",
        "الثالث": "C", "ثالث": "C", "التاء": "C", "ت": "C", "3": "C",
        "الرابع": "D", "رابع": "D", "الثاء": "D", "ث": "D", "4": "D",
        "الخامس": "E", "خامس": "E", "الجيم": "E", "ج": "E", "5": "E",
        "first": "A", "1st": "A", 
        "second": "B", "2nd": "B",
        "third": "C", "3rd": "C",
        "fourth": "D", "4th": "D",
        "fifth": "E", "5th": "E"
    }
    if clean_answer in text_answers:
        clean_answer = text_answers[clean_answer]
        if clean_answer in label_to_idx:
            return question, [text for _, text in options], label_to_idx[clean_answer]
    
    return None

def parse_mcq(text: str) -> List[Tuple[str, List[str], int]]:
    # تقسيم النص إلى كتل: إما بفاصل أسطر فارغة أو ببداية سؤال جديد
    blocks = []
    current_block = []
    
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
            continue
        
        # إذا كان السطر يبدأ بنمط سؤال (مثل "Q", "سؤال", رقم، إلخ) ونحن في كتلة حالية، نبدأ كتلة جديدة
        if current_block and re.match(r'^\s*(?:[Qس]|\d+[.)]|\[)', stripped, re.I):
            blocks.append("\n".join(current_block))
            current_block = [stripped]
        else:
            current_block.append(stripped)
    
    if current_block:
        blocks.append("\n".join(current_block))
    
    results = []
    for block in blocks:
        parsed = parse_single_mcq(block)
        if parsed:
            results.append(parsed)
        else:
            # محاولة تقسيم الكتل التي تحتوي على أسئلة متعددة بدون فواصل
            # بنظام: سؤال ثم خيارات ثم إجابة، ثم سؤال آخر... بدون أسطر فارغة
            # هذه محاولة إضافية قد تزيد من المرونة
            sub_blocks = re.split(r'(?=^\s*(?:[Qس]|\d+[.)]|\[))', block, flags=re.M | re.I)
            for sub_block in sub_blocks:
                if sub_block.strip():
                    parsed_sub = parse_single_mcq(sub_block)
                    if parsed_sub:
                        results.append(parsed_sub)
    
    return results

# ------------------------------------------------------------------
# 9. Sender task (single per chat, cancellable)
# ------------------------------------------------------------------
sender_tasks: Dict[int, asyncio.Task] = {}

async def _sender(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, is_private: bool) -> None:
    """Dedicated long-running sender for each chat."""
    try:
        while True:
            q, opts, idx, quiz_id, to_delete = await send_queues[chat_id].get()
            async with semaphores[chat_id]:
                try:
                    poll = await context.bot.send_poll(
                        chat_id=chat_id,
                        question=q,
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
                            "INSERT OR IGNORE INTO quizzes(quiz_id, question, options, correct_option, user_id) VALUES (?,?,?,?,?)",
                            (quiz_id, q, ':::'.join(opts), idx, user_id),
                        )
                        await conn.commit()

                    bot_username = (await context.bot.get_me()).username
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

                    if is_private:
                        await conn.execute("INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES (?,0)", (user_id,))
                        await conn.execute("UPDATE user_stats SET sent=sent+1 WHERE user_id=?", (user_id,))
                    else:
                        await conn.execute("INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?,0)", (chat_id,))
                        await conn.execute("UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?", (chat_id,))
                    await conn.commit()

                    await asyncio.sleep(SEND_INTERVAL)
                except telegram.error.BadRequest as e:
                    logger.warning(f"BadRequest while sending poll: {e}")
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.exception(f"Error sending poll: {e}")
                    await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info(f"Sender task cancelled for {chat_id}")
        raise

def ensure_sender(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, is_private: bool) -> None:
    """Start a sender task if not running."""
    if chat_id not in sender_tasks or sender_tasks[chat_id].done():
        if chat_id in sender_tasks and sender_tasks[chat_id].done():
            logger.warning(f"Restarting sender task for {chat_id}")
        sender_tasks[chat_id] = asyncio.create_task(_sender(chat_id, context, user_id, is_private))

# ------------------------------------------------------------------
# 10. Enqueue wrapper with validation
# ------------------------------------------------------------------
async def enqueue_mcq(msg, context, override=None, is_private=False) -> bool:
    uid = msg.from_user.id
    conn = await DB.conn()
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone()
    default_channel = row[0] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or msg.chat_id)

    lang = (msg.from_user.language_code or "en")[:2]
    text = msg.text or msg.caption or ""
    
    # Handle parsing errors
    try:
        results = parse_mcq(text)
    except Exception as e:
        logger.exception(f"Parsing failed: {e}")
        if is_private:
            await msg.reply_text(get_text("invalid_format", lang))
        return False

    if not results:
        if is_private:
            await msg.reply_text(get_text("no_q", lang))
        return False

    success = False
    for q, opts, idx in results:
        # Validate question and options
        if not validate_mcq(q, opts):
            logger.warning(f"Invalid MCQ: question or options too long. Q: {len(q)}, opts: {[len(o) for o in opts]}")
            if is_private:
                await msg.reply_text(get_text("invalid_format", lang))
            continue
            
        quiz_id = hashlib.md5((q + ':::' + ':::'.join(opts)).encode()).hexdigest()
        try:
            send_queues[cid].put_nowait((q, opts, idx, quiz_id, msg.message_id if is_private else None))
            success = True
        except asyncio.QueueFull:
            logger.warning(f"Queue full for chat {cid}")
            if is_private:
                await msg.reply_text(get_text("queue_full", lang))
            return False
        ensure_sender(cid, context, uid, is_private)
    return success

# ------------------------------------------------------------------
# 11. Handlers
# ------------------------------------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not (msg.text or msg.caption):
        return
    if update.effective_chat.type == ChatType.PRIVATE:
        await enqueue_mcq(msg, context, is_private=True)
        return
    bot_username = (await context.bot.get_me()).username.lower()
    text = (msg.text or msg.caption or "").lower()
    is_reply = msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id
    is_mention = f"@{bot_username}" in text
    if is_reply or is_mention:
        await enqueue_mcq(msg, context, is_private=False)

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return
    conn = await DB.conn()
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
    lang = (update.effective_user.language_code or "en")[:2]
    kb = [[InlineKeyboardButton("📝 New Question", callback_data="new")],
          [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
          [InlineKeyboardButton("📘 Help", callback_data="help")]]
    await update.message.reply_text(get_text("start", lang), reply_markup=InlineKeyboardMarkup(kb))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    cmd = query.data
    uid = update.effective_user.id
    lang = (update.effective_user.language_code or "en")[:2]
    conn = await DB.conn()

    if cmd == "stats":
        r = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (uid,))).fetchone()
        s = await (await conn.execute("SELECT SUM(sent) FROM channel_stats")).fetchone()
        txt = get_text("stats", lang, pr=r[0] if r else 0, ch=s[0] if s else 0)
    elif cmd == "help":
        txt = get_text("help", lang)
    else:
        txt = "⚠️ Unsupported"
    await query.edit_message_text(txt)

# ------------------------------------------------------------------
# 12. Database initialization
# ------------------------------------------------------------------
async def init_db() -> None:
    conn = await DB.conn()
    await conn.execute("CREATE TABLE IF NOT EXISTS user_stats(user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS channel_stats(chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS known_channels(chat_id INTEGER PRIMARY KEY, title TEXT)")
    await conn.execute("CREATE TABLE IF NOT EXISTS quizzes(quiz_id TEXT PRIMARY KEY, question TEXT, options TEXT, correct_option INTEGER, user_id INTEGER)")
    await conn.execute("CREATE TABLE IF NOT EXISTS default_channels(user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)")
    await conn.commit()
    logger.info("✅ DB initialized")

# ------------------------------------------------------------------
# 13. Startup / Shutdown
# ------------------------------------------------------------------
async def post_init(app: Application) -> None:
    await init_db()
    asyncio.create_task(schedule_cleanup())
    logger.info("✅ Bot started successfully")

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
            logger.error(f"Cleanup error: {e}")

async def shutdown(app: Application) -> None:
    logger.info("🛑 Shutting down...")
    for t in sender_tasks.values():
        t.cancel()
    await asyncio.gather(*sender_tasks.values(), return_exceptions=True)
    await DB.close()
    logger.info("✅ Graceful shutdown completed")

# ------------------------------------------------------------------
# 14. Main entry point
# ------------------------------------------------------------------
def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("❌ TELEGRAM_BOT_TOKEN missing")

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(app)))

    logger.info("✅ Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
