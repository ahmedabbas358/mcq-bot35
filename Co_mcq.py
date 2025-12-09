# bot.py — production-ready Telegram MCQ bot (async, PTB modern API)
import os
import re
import logging
import asyncio
import hashlib
import signal
import contextlib
from collections import defaultdict
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
# Config (env)
# ---------------------------
DB_PATH = os.getenv("DB_PATH", "stats.db")
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "50"))
SEND_INTERVAL = float(os.getenv("SEND_INTERVAL", "0.75"))         # seconds between polls
MAX_CONCURRENT_SEND = int(os.getenv("MAX_CONCURRENT_SEND", "5"))  # parallel polls per chat
MAX_QUESTION_LENGTH = int(os.getenv("MAX_QUESTION_LENGTH", "300"))
MAX_OPTION_LENGTH = int(os.getenv("MAX_OPTION_LENGTH", "100"))

# ---------------------------
# Texts & translations (minimal)
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
    }
}

def get_text(key: str, lang: str = "en", **kwargs) -> str:
    lang_key = lang[:2] if lang else "en"
    text = TEXTS.get(key, {}).get(lang_key) or TEXTS.get(key, {}).get("en", "")
    return text.format(**kwargs)

# ---------------------------
# Utilities
# ---------------------------
def log_memory_usage():
    mem = psutil.Process().memory_info().rss / (1024 * 1024)
    logger.info(f"Memory usage: {mem:.2f} MB")

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

def parse_single_mcq(block: str) -> Optional[Tuple[str, List[str], int]]:
    block = re.sub(r'[\u200b\u200c\ufeff]', '', block)
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if len(lines) > 80:
        return None

    question = None
    options: List[Tuple[str, str]] = []
    answer_label = None

    question_prefixes = QUESTION_PREFIXES + ["MCQ", "Multiple Choice", "اختبار", "اختر", "أسئلة", "Questions", "السؤال"]
    option_patterns = [
        r'^\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])\s*[).:\-]\s*(.+)',
        r'^\s*[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]\s*(.+)',
        r'^\s*[\u25cb\u25cf\u25a0\u2022\u00d8\*]\s*([a-zأ-ي0-9])\s*[:.]?\s*(.+)',
        r'^\s*([a-zأ-ي0-9])\s*[\u2013\u2014]\s*(.+)',
        r'^\s*\b(?:option|اختيار)\s*([a-zأ-ي0-9])\s*[:.]\s*(.+)'
    ]
    answer_keywords = ANSWER_KEYWORDS + ["Correct", "Solution", "Key", "مفتاح", "صحيح", "صح", "الحل"]

    for line in lines:
        if question is None:
            for p in question_prefixes:
                if line.lower().startswith(p.lower()):
                    question = re.sub(f'^{re.escape(p)}\\s*[:.\\-]?\\s*', '', line, flags=re.I).strip()
                    break
            if question is not None:
                continue

        # options
        matched = False
        for pat in option_patterns:
            m = re.match(pat, line, re.I | re.U)
            if m:
                label, text = m.groups()
                label = ''.join(ARABIC_DIGITS.get(c, c) for c in label).upper()
                label = ''.join(ARABIC_LETTERS.get(c, c) for c in label).strip()
                if label:
                    options.append((label, text.strip()))
                    matched = True
                    break
        if matched:
            continue

        # answer line
        if answer_label is None:
            for kw in answer_keywords:
                if kw.lower() in line.lower():
                    # try to capture label
                    patterns = [
                        r'[:：]\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])$',
                        r'is\s+([a-zأ-ي0-9])',
                        r'هي\s+([a-zأ-ي0-9])',
                        r'[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]$',
                        r'\b(?:correct|صح|صحيح)\s*[:\-]\s*([a-zأ-ي0-9])',
                        r'[\u2714\u2705]\s*([a-zأ-ي0-9])'
                    ]
                    for pat in patterns:
                        m = re.search(pat, line, re.I | re.U)
                        if m:
                            answer_label = m.group(1)
                            answer_label = ''.join(ARABIC_DIGITS.get(c, c) for c in answer_label).upper()
                            answer_label = ''.join(ARABIC_LETTERS.get(c, c) for c in answer_label).strip()
                            break
                    break

    if question is None and lines:
        question = lines[0]
        if options and lines[0].startswith(options[0][0]):
            question = None

    if not question or not options or not answer_label:
        return None

    # map labels
    label_to_idx: Dict[str, int] = {}
    for idx, (label, text) in enumerate(options):
        clean = re.sub(r'[^A-Z0-9]', '', label)
        label_to_idx[clean] = idx
        if clean.isdigit() and 1 <= int(clean) <= 26:
            label_to_idx[chr(64 + int(clean))] = idx

    clean_ans = re.sub(r'[^A-Z0-9]', '', answer_label)
    if clean_ans in label_to_idx:
        return question, [t for _, t in options], label_to_idx[clean_ans]

    # fallback textual answers
    text_answers = {
        "الأول": "A", "أول": "A", "أ": "A", "1": "A",
        "الثاني": "B", "ثاني": "B", "ب": "B", "2": "B",
        "الثالث": "C", "ثالث": "C", "ت": "C", "3": "C",
        "الرابع": "D", "رابع": "D", "ث": "D", "4": "D",
        "الخامس": "E", "خامس": "E", "ج": "E", "5": "E",
        "first": "A", "1st": "A", "second": "B", "2nd": "B",
    }
    if clean_ans in text_answers:
        mapped = text_answers[clean_ans]
        if mapped in label_to_idx:
            return question, [t for _, t in options], label_to_idx[mapped]

    return None

def parse_mcq(text: str) -> List[Tuple[str, List[str], int]]:
    blocks: List[str] = []
    cur: List[str] = []
    lines = text.splitlines()
    for line in lines:
        s = line.strip()
        if not s:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
            continue
        if cur and re.match(r'^\s*(?:[Qس]|\d+[.)]|\[)', s, re.I):
            blocks.append("\n".join(cur))
            cur = [s]
        else:
            cur.append(s)
    if cur:
        blocks.append("\n".join(cur))

    out: List[Tuple[str, List[str], int]] = []
    for b in blocks:
        p = parse_single_mcq(b)
        if p:
            out.append(p)
        else:
            subs = re.split(r'(?=^\s*(?:[Qس]|\d+[.)]|\[))', b, flags=re.M | re.I)
            for sb in subs:
                if sb.strip():
                    ps = parse_single_mcq(sb)
                    if ps:
                        out.append(ps)
    return out

# ---------------------------
# Queues, semaphores, senders
# ---------------------------
SendItem = Tuple[str, List[str], int, str, Optional[int]]
send_queues: Dict[int, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=MAX_QUEUE_SIZE))
semaphores: Dict[int, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(MAX_CONCURRENT_SEND))
sender_tasks: Dict[int, asyncio.Task] = {}

async def _sender(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, is_private: bool) -> None:
    logger.info(f"Sender task started for chat {chat_id}")
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

                    # share / repost buttons
                    bot_username = context.bot_data.get("bot_username")
                    if not bot_username:
                        bot_username = (await context.bot.get_me()).username
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

                    # stats update
                    if is_private:
                        await conn.execute("INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES (?,0)", (user_id,))
                        await conn.execute("UPDATE user_stats SET sent=sent+1 WHERE user_id=?", (user_id,))
                    else:
                        await conn.execute("INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?,0)", (chat_id,))
                        await conn.execute("UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?", (chat_id,))
                    await conn.commit()

                    await asyncio.sleep(SEND_INTERVAL)
                except telegram.error.BadRequest as e:
                    logger.warning(f"BadRequest while sending poll to {chat_id}: {e}")
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.exception(f"Error sending poll to {chat_id}: {e}")
                    await asyncio.sleep(5)
    except asyncio.CancelledError:
        logger.info(f"Sender task cancelled for {chat_id}")
        raise

def ensure_sender(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int, is_private: bool) -> None:
    if chat_id not in sender_tasks or sender_tasks[chat_id].done():
        sender_tasks[chat_id] = asyncio.create_task(_sender(chat_id, context, user_id, is_private))

# ---------------------------
# Enqueue wrapper
# ---------------------------
async def enqueue_mcq(msg, context, override=None, is_private=False) -> bool:
    uid = msg.from_user.id if msg.from_user else 0
    conn = await DB.conn()
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone() if uid else None
    default_channel = row[0] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or (msg.chat_id if hasattr(msg, "chat_id") else msg.chat.id))

    lang = (msg.from_user.language_code or "en")[:2] if msg.from_user else "en"
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
            logger.warning(f"Invalid MCQ (len). Q:{len(q)} opts:{[len(o) for o in opts]}")
            if is_private and hasattr(msg, "reply_text"):
                await msg.reply_text(get_text("invalid_format", lang))
            continue

        quiz_id = hashlib.md5((q + ':::' + ':::'.join(opts)).encode()).hexdigest()
        try:
            send_queues[cid].put_nowait((q, opts, idx, quiz_id, msg.message_id if is_private and hasattr(msg, "message_id") else None))
            success = True
        except asyncio.QueueFull:
            logger.warning(f"Queue full for chat {cid}")
            if is_private and hasattr(msg, "reply_text"):
                await msg.reply_text(get_text("queue_full", lang))
            return False
        ensure_sender(cid, context, uid, is_private)
    return success

# ---------------------------
# Handlers
# ---------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not (msg.text or msg.caption):
        return
    if update.effective_chat.type == ChatType.PRIVATE:
        await enqueue_mcq(msg, context, is_private=True)
        return

    bot_username = context.bot_data.get("bot_username")
    if not bot_username:
        bot_username = (await context.bot.get_me()).username
        context.bot_data["bot_username"] = bot_username
    text = (msg.text or msg.caption or "").lower()
    is_reply = bool(msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == (await context.bot.get_me()).id)
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
    found = await enqueue_mcq(post, context, is_private=False)
    if not found:
        # channel posts may not have from_user
        lang = "en"
        await context.bot.send_message(post.chat.id, get_text("no_q", lang))

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
        txt = get_text("stats", lang, pr=r[0] if r else 0, ch=s[0] if s and s[0] else 0)
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
    await conn.execute("CREATE TABLE IF NOT EXISTS quizzes(quiz_id TEXT PRIMARY KEY, question TEXT, options TEXT, correct_option INTEGER, user_id INTEGER)")
    await conn.execute("CREATE TABLE IF NOT EXISTS default_channels(user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)")
    await conn.commit()
    logger.info("✅ DB initialized")

async def schedule_cleanup() -> None:
    while True:
        await asyncio.sleep(86400)  # daily
        try:
            conn = await DB.conn()
            await conn.execute("DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent>0)")
            await conn.execute("DELETE FROM user_stats WHERE sent=0")
            await conn.commit()
            logger.info("✅ DB cleanup")
        except Exception as e:
            logger.exception(f"Cleanup error: {e}")

# ---------------------------
# Startup / Shutdown
# ---------------------------
async def post_init(app: "telegram.ext.Application") -> None:
    # run DB init and background tasks
    await init_db()
    # store bot info
    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username
    app.bot_data["bot_id"] = me.id
    # start cleanup background task
    app.create_task(schedule_cleanup())
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

    app = ApplicationBuilder().token(token).build()

    # handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # post_init
    app.post_init(post_init)

    # graceful shutdown on signals (Unix)
    loop = asyncio.get_event_loop()
    try:
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda sig=s: asyncio.create_task(shutdown(app)))
    except NotImplementedError:
        # Windows or restricted env — ignore
        pass

    logger.info("✅ Bot starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
