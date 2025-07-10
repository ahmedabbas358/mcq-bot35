import os
import re
import logging
import asyncio
import time
import hashlib
from collections import defaultdict, deque
from typing import List, Tuple, Optional

import aiosqlite
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Poll,
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

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Global variables
DB_PATH = os.getenv("DB_PATH", "stats.db")
send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)
_db_conn: aiosqlite.Connection = None
quiz_cache = defaultdict(set)  # Cache to prevent duplicates, per chat

# Character mappings
ARABIC_DIGITS = {"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"}
ARABIC_DIGITS.update({str(i): str(i) for i in range(10)})
EN_LETTERS = {chr(ord("A") + i): i for i in range(12)}  # A-L
EN_LETTERS.update({chr(ord("a") + i): i for i in range(12)})
AR_LETTERS = {"أ": 0, "ب": 1, "ج": 2, "د": 3, "هـ": 4, "و": 5, "ز": 6, "ح": 7, "ط": 8, "ي": 9, "ك": 10, "ل": 11}

# Regex patterns for MCQ parsing
PATTERNS = [
    # English Q: with A), B), etc.
    re.compile(
        r"(?:Q|Question)[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-J][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?P<expl>(?:Explanation|تفسير)[:：]?\s*.+?)?\s*"
        r"(?:Answer|Ans|Correct Answer|الإجابة\s*الصحيحة|الإجابة|الجواب)[:：]?\s*(?P<ans>[A-J0-9])",
        re.S | re.UNICODE,
    ),
    # Arabic س: with أ), ب), etc.
    re.compile(
        r"(?:س|سؤال)[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-ي][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?P<expl>(?:Explanation|تفسير)[:：]?\s*.+?)?\s*"
        r"(?:Answer|Ans|Correct Answer|الإجابة\s*الصحيحة|الإجابة|الجواب)[:：]?\s*(?P<ans>[أ-ي١-٩])",
        re.S | re.UNICODE,
    ),
    # No prefix, with A), B), or 1), 2), etc.
    re.compile(
        r"(?P<q>.+?)\s*(?P<opts>(?:[A-Za-zأ-ي0-9١-٩][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?P<expl>(?:Explanation|تفسير)[:：]?\s*.+?)?\s*"
        r"(?:Answer|Ans|الإجابة|Correct Answer|الجواب)[:：]?\s*(?P<ans>[A-Za-zأ-ي0-9١-٩])",
        re.S | re.UNICODE,
    ),
    # Arabic with dot-separated options (أ., ب., etc.)
    re.compile(
        r"(?:س|سؤال)[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-ي]\.\s*.+?(?:\n|$)){2,10})"
        r"(?P<expl>(?:Explanation|تفسير)[:：]?\s*.+?)?\s*"
        r"(?:Answer|Ans|Correct Answer|الإجابة\s*الصحيحة|الإجابة|الجواب)[:：]?\s*(?P<ans>[أ-ي١-٩])",
        re.S | re.UNICODE,
    ),
    # Numeric options (1., 2., 1), 2), etc.)
    re.compile(
        r"(?:Q|س|Question|سؤال)?[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:\d+[).:]\s*.+?(?:\n|$)){2,10})"
        r"(?P<expl>(?:Explanation|تفسير)[:：]?\s*.+?)?\s*"
        r"(?:Answer|Ans|الإجابة|Correct Answer|الجواب)[:：]?\s*(?P<ans>[0-9١-٩])",
        re.S | re.UNICODE,
    ),
    # Simple letter/number options (A , 1 , etc.)
    re.compile(
        r"(?:Q|س|Question|سؤال)?[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-Za-zأ-ي0-9١-٩]\s+.+?(?:\n|$)){2,10})"
        r"(?P<expl>(?:Explanation|تفسير)[:：]?\s*.+?)?\s*"
        r"(?:Answer|Ans|الإجابة|Correct Answer|الجواب)[:：]?\s*(?P<ans>[A-Za-zأ-ي0-9١-٩])",
        re.S | re.UNICODE,
    ),
]

# Translated messages
TEXTS = {
    "start": {"en": "🤖 Welcome! Choose an option:", "ar": "🤖 أهلاً! اختر خيارًا:"},
    "help": {
        "en": "🆘 Usage:\n- Send MCQ in private.\n- Publish to channel: use 🔄 or /setchannel.\n- In groups: reply or mention bot.\nExample:\nQ: ...\nA) ...\nB) ...\nAnswer: A",
        "ar": "🆘 الاستخدام:\n- أرسل MCQ في الخاص.\n- انشر في قناة: استخدم 🔄 أو /setchannel.\n- في المجموعات: رد أو اذكر البوت.\nمثال:\nس: ...\nأ) ...\nب) ...\nالإجابة: أ",
    },
    "new": {"en": "📝 Send your MCQ now!", "ar": "📝 أرسل سؤال MCQ الآن!"},
    "stats": {
        "en": "📊 Private: {pr} questions.\n🏷️ Channels: {ch} posts.",
        "ar": "📊 خاص: {pr} سؤال.\n🏷️ قنوات: {ch} منشور.",
    },
    "queue_full": {"en": "🚫 Queue full, try later.", "ar": "🚫 القائمة ممتلئة، حاول لاحقًا."},
    "no_q": {
        "en": "❌ No valid questions found.\n\nExample:\nQ: What is the capital of France?\nA) London\nB) Paris\nAnswer: B",
        "ar": "❌ لم يتم العثور على أسئلة صحيحة.\n\nمثال:\nس: ما هي عاصمة فرنسا؟\nأ) لندن\nب) باريس\nالإجابة: ب",
    },
    "invalid_format": {"en": "⚠️ Invalid MCQ format.", "ar": "⚠️ صيغة MCQ غير صحيحة."},
    "duplicate_options": {"en": "⚠️ Duplicate options detected in MCQ.", "ar": "⚠️ تم اكتشاف خيارات مكررة في MCQ."},
    "quiz_sent": {"en": "✅ Quiz sent!", "ar": "✅ تم إرسال الاختبار!"},
    "share_quiz": {"en": "📢 Share Quiz", "ar": "📢 مشاركة الاختبار"},
    "repost_quiz": {"en": "🔄 Repost Quiz", "ar": "🔄 إعادة نشر الاختبار"},
    "channels_list": {"en": "📺 Channels:\n{channels}", "ar": "📺 القنوات:\n{channels}"},
    "no_channels": {"en": "❌ No channels found.", "ar": "❌ لا توجد قنوات."},
    "private_channel_warning": {
        "en": "⚠️ Ensure bot is admin in private channels.",
        "ar": "⚠️ تأكد أن البوت مشرف في القنوات الخاصة.",
    },
    "set_channel_success": {"en": "✅ Default channel: {title}", "ar": "✅ القناة الافتراضية: {title}"},
    "no_channel_selected": {"en": "❌ No channel selected.", "ar": "❌ لم يتم اختيار قناة."},
    "language_set": {"en": "🌐 Language set to {lang}.", "ar": "🌐 اللغة محددة إلى {lang}."},
    "my_quizzes": {"en": "📚 Your quizzes (Page {page}/{total}):\n{quizzes}", "ar": "📚 أسئلتك (الصفحة {page}/{total}):\n{quizzes}"},
    "no_quizzes": {"en": "❌ No quizzes found.", "ar": "❌ لا توجد أسئلة."},
    "rate_limit": {"en": "🚫 Please wait 2 seconds before sending another quiz.", "ar": "🚫 يرجى الانتظار 2 ثانية قبل إرسال اختبار آخر."},
    "post_failed": {"en": "❌ Failed to send quiz after retries.", "ar": "❌ فشل إرسال الاختبار بعد المحاولات."},
    "not_admin": {"en": "⚠️ Bot must be an admin with posting permissions.", "ar": "⚠️ يجب أن يكون البوت مشرفًا بصلاحيات النشر."},
    "edit_quiz_prompt": {"en": "📝 Send the updated MCQ for quiz ID: {quiz_id}", "ar": "📝 أرسل MCQ المحدث لمعرف الاختبار: {quiz_id}"},
    "edit_quiz_success": {"en": "✅ Quiz updated successfully!", "ar": "✅ تم تحديث الاختبار بنجاح!"},
}

def get_text(key: str, lang: str, **kwargs) -> str:
    """Retrieve translated text with English fallback."""
    return TEXTS[key].get(lang, TEXTS[key]["en"]).format(**kwargs)

async def get_db() -> aiosqlite.Connection:
    """Get singleton database connection and ensure initialization."""
    global _db_conn
    if _db_conn is None:
        try:
            _db_conn = await aiosqlite.connect(DB_PATH)
            await init_db(_db_conn)
            await _db_conn.execute("CREATE INDEX IF NOT EXISTS idx_quiz_id ON quizzes (quiz_id)")
            await _db_conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise
    return _db_conn

async def close_db() -> None:
    """Close database connection."""
    global _db_conn
    if _db_conn:
        await _db_conn.close()
        _db_conn = None

async def init_db(conn: aiosqlite.Connection) -> None:
    """Initialize database tables."""
    try:
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
            "CREATE TABLE IF NOT EXISTS quizzes (quiz_id TEXT PRIMARY KEY, question TEXT, options TEXT, correct_option INTEGER, user_id INTEGER, explanation TEXT)"
        )
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS default_channels (user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)"
        )
        await conn.commit()
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise

async def schedule_cleanup() -> None:
    """Periodically clean unused database entries and in-memory cache."""
    while True:
        try:
            await asyncio.sleep(86400)  # 1 day
            conn = await get_db()
            await conn.execute(
                "DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent > 0)"
            )
            await conn.execute("DELETE FROM user_stats WHERE sent = 0")
            await conn.commit()
            quiz_cache.clear()
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")

def escape_markdown(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    return re.sub(r'([_*[\]()~`>#+\-=|{}.!])', r'\\\1', text)

def parse_mcq(text: str) -> List[Tuple[str, List[str], int, Optional[str]]]:
    """Parse MCQ text into question, options, correct answer index, and optional explanation."""
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = escape_markdown(m.group("q").strip())
            raw = m.group("opts")
            expl = m.group("expl").strip() if m.group("expl") else None
            if expl:
                expl = escape_markdown(expl.replace("Explanation:", "").replace("تفسير:", "").strip())
            opts = [escape_markdown(opt.strip()) for opt in re.findall(r"(?:[A-Za-zأ-ي0-9١-٩][).:]|\d+[).:]|[A-Za-zأ-ي0-9١-٩]\s+|[أ-ي]\.)\s*(.+)", raw, re.UNICODE)]
            if not (2 <= len(opts) <= 10):
                logger.debug(f"Invalid number of options: {len(opts)} for question: {q[:50]}...")
                continue
            opt_set = set(opt.lower() for opt in opts)
            if len(opt_set) != len(opts):
                logger.debug(f"Duplicate options detected in question: {q[:50]}...")
                return []
            ans = m.group("ans").strip()
            idx = None
            if ans in ARABIC_DIGITS:
                idx = int(ARABIC_DIGITS[ans])
            elif ans in EN_LETTERS:
                idx = EN_LETTERS[ans]
            elif ans in AR_LETTERS:
                idx = AR_LETTERS[ans]
            else:
                try:
                    idx = int(ans) - 1
                except ValueError:
                    logger.debug(f"Invalid answer format: {ans} for question: {q[:50]}...")
                    continue
            if not (0 <= idx < len(opts)):
                logger.debug(f"Answer index out of range: {idx} for question: {q[:50]}...")
                continue
            res.append((q, opts, idx, expl))
    if not res:
        logger.debug(f"No valid MCQs parsed from text: {text[:100]}...")
    return res

async def build_main_menu(lang: str, state: str = "main") -> InlineKeyboardMarkup:
    """Build stateful inline keyboard menu."""
    kb = []
    if state == "main":
        kb = [
            [InlineKeyboardButton("📝 New Quiz", callback_data="new")],
            [InlineKeyboardButton("🔄 Publish to Channel", callback_data="publish_channel")],
            [InlineKeyboardButton("📊 My Stats", callback_data="stats")],
            [InlineKeyboardButton("📺 Channels", callback_data="channels")],
            [InlineKeyboardButton("📘 Help", callback_data="help")],
        ]
    elif state == "publish_channel":
        conn = await get_db()
        rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        kb = [[InlineKeyboardButton(t, callback_data=f"choose_{cid}")] for cid, t in rows]
        kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
    return InlineKeyboardMarkup(kb)

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE, user_id: int = None, is_private: bool = False, quiz_id: str = None) -> None:
    """Process send queue for a chat."""
    conn = await get_db()
    retries = defaultdict(int)
    max_retries = 3
    while send_queues[chat_id]:
        q, opts, idx, expl = send_queues[chat_id].popleft()
        quiz_hash = hashlib.md5((q + ':::'.join(opts)).encode()).hexdigest()
        if quiz_hash in quiz_cache[chat_id]:
            logger.debug(f"Skipped duplicate quiz in cache for chat {chat_id}: {q[:50]}...")
            continue
        row = await (await conn.execute("SELECT quiz_id FROM quizzes WHERE quiz_id=?", (quiz_hash,))).fetchone()
        if row:
            logger.debug(f"Skipped duplicate quiz in database for chat {chat_id}: {q[:50]}...")
            quiz_cache[chat_id].add(quiz_hash)
            continue
        quiz_cache[chat_id].add(quiz_hash)
        try:
            if not is_private:
                try:
                    bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
                    if bot_member.status not in ["administrator", "creator"] or not bot_member.can_post_messages:
                        await context.bot.send_message(chat_id, get_text("not_admin", context.user_data.get("lang", "en")))
                        continue
                except Exception as e:
                    logger.error(f"Failed to verify bot permissions in chat {chat_id}: {e}")
                    await context.bot.send_message(chat_id, get_text("not_admin", context.user_data.get("lang", "en")))
                    continue
            poll = await context.bot.send_poll(
                chat_id,
                q,
                opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False,
                parse_mode="MarkdownV2",
            )
            await asyncio.sleep(0.5)
            msg_id = context.user_data.pop("message_to_delete", None)
            if msg_id:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception as e:
                    logger.warning(f"Failed to delete message {msg_id} in chat {chat_id}: {e}")
            if not quiz_id:
                quiz_id = quiz_hash
                await conn.execute(
                    "INSERT OR IGNORE INTO quizzes (quiz_id, question, options, correct_option, user_id, explanation) VALUES (?, ?, ?, ?, ?, ?)",
                    (quiz_id, q, ':::'.join(opts), idx, user_id, expl),
                )
                await conn.commit()
            lang = context.user_data.get("lang", "en")
            bot_username = (await context.bot.get_me()).username
            share_link = f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
            keyboard = [
                [InlineKeyboardButton(get_text("share_quiz", lang), url=share_link)],
                [InlineKeyboardButton(get_text("repost_quiz", lang), callback_data=f"repost_{quiz_id}")],
            ]
            message = get_text("quiz_sent", lang)
            if expl:
                message += f"\n\n**Explanation**: {expl}"
            await context.bot.send_message(
                chat_id,
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                reply_to_message_id=poll.message_id,
                parse_mode="MarkdownV2",
            )
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
            retries[quiz_hash] += 1
            if retries[quiz_hash] >= max_retries:
                logger.error(f"Failed to send quiz after {max_retries} retries in chat {chat_id}: {q[:50]}...")
                await context.bot.send_message(chat_id, get_text("post_failed", lang))
                continue
            logger.error(f"Error processing quiz (chat_id={chat_id}, user_id={user_id}): {e}")
            send_queues[chat_id].appendleft((q, opts, idx, expl))
            break

async def enqueue_mcq(msg: Update.message, context: ContextTypes.DEFAULT_TYPE, override: int = None, is_private: bool = False) -> bool:
    """Enqueue MCQ for processing."""
    uid = msg.from_user.id
    lang = context.user_data.get("lang", (msg.from_user.language_code or "en")[:2])
    if time.time() - last_sent_time[uid] < 2:
        await context.bot.send_message(msg.chat.id, get_text("rate_limit", lang))
        return False
    last_sent_time[uid] = time.time()
    conn = await get_db()
    row = await (await conn.execute("SELECT chat_id FROM default_channels WHERE user_id=?", (uid,))).fetchone()
    default_channel = row[0] if row else None
    cid = override or context.chat_data.get("target_channel", default_channel or msg.chat.id)
    if len(send_queues[cid]) > 50:
        await context.bot.send_message(cid, get_text("queue_full", lang))
        return False
    text = (msg.text or msg.caption or "").strip()
    logger.debug(f"Received text for MCQ parsing in chat {cid}: {text[:100]}...")
    if not text:
        return False
    blocks = [b for b in re.split(r"\n{2,}", text) if b.strip()]
    found = False
    editing_quiz_id = context.user_data.get("editing_quiz_id")
    for blk in blocks:
        parsed_mcqs = parse_mcq(blk)
        if not parsed_mcqs and "Duplicate options" in blk:
            await context.bot.send_message(cid, get_text("duplicate_options", lang))
            return False
        for q, o, i, expl in parsed_mcqs:
            quiz_hash = hashlib.md5((q + ':::'.join(o)).encode()).hexdigest()
            if quiz_hash in quiz_cache[cid]:
                logger.debug(f"Skipped duplicate quiz in enqueue_mcq for chat {cid}: {q[:50]}...")
                continue
            row = await (await conn.execute("SELECT quiz_id FROM quizzes WHERE quiz_id=?", (quiz_hash,))).fetchone()
            if row and not editing_quiz_id:
                logger.debug(f"Skipped duplicate quiz in database for chat {cid}: {q[:50]}...")
                quiz_cache[cid].add(quiz_hash)
                continue
            send_queues[cid].append((q, o, i, expl))
            found = True
            if editing_quiz_id:
                await conn.execute(
                    "UPDATE quizzes SET question=?, options=?, correct_option=?, explanation=? WHERE quiz_id=? AND user_id=?",
                    (q, ':::'.join(o), i, expl, editing_quiz_id, uid),
                )
                await conn.commit()
                context.user_data.pop("editing_quiz_id", None)
                await context.bot.send_message(cid, get_text("edit_quiz_success", lang))
    if found:
        context.user_data["message_to_delete"] = msg.message_id
        asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=is_private))
    return found

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages."""
    msg = update.message
    if not msg or not (msg.text or msg.caption):
        return
    chat_type = msg.chat.type
    lang = context.user_data.get("lang", (msg.from_user.language_code or "en")[:2])
    text = (msg.text or msg.caption or "").strip()

    # Handle reply keyboard options
    reply_keyboard_options = ["📝 New Quiz", "🔄 Publish to Channel", "📊 My Stats", "📺 Channels", "📘 Help"]
    if text in reply_keyboard_options:
        if text == "📝 New Quiz":
            await update.message.reply_text(get_text("new", lang))
            context.user_data["awaiting_mcq"] = True
        elif text == "🔄 Publish to Channel":
            conn = await get_db()
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if rows:
                kb = [[InlineKeyboardButton(t, callback_data=f"choose_{cid}")] for cid, t in rows]
                kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
                await update.message.reply_text("Choose a channel:", reply_markup=InlineKeyboardMarkup(kb))
            else:
                await update.message.reply_text(get_text("no_channels", lang))
        elif text == "📊 My Stats":
            uid = update.effective_user.id
            conn = await get_db()
            r = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (uid,))).fetchone()
            s = await (await conn.execute("SELECT SUM(sent) FROM channel_stats")).fetchone()
            await update.message.reply_text(get_text("stats", lang, pr=r[0] if r else 0, ch=s[0] if s else 0))
        elif text == "📺 Channels":
            conn = await get_db()
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            txt = get_text("no_channels", lang) if not rows else get_text("channels_list", lang, channels="\n".join(f"- {t}: {cid}" for cid, t in rows))
            await update.message.reply_text(txt)
        elif text == "📘 Help":
            await update.message.reply_text(get_text("help", lang))
        return

    # Process as MCQ
    if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if not parse_mcq(text):
            return
    if chat_type == ChatType.PRIVATE and context.user_data.get("awaiting_mcq"):
        found = await enqueue_mcq(msg, context, is_private=True)
        if found:
            conn = await get_db()
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if rows:
                kb = [[InlineKeyboardButton(t, callback_data=f"post_to_{cid}")] for cid, t in rows]
                kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
                await msg.reply_text("Choose where to post:", reply_markup=InlineKeyboardMarkup(kb))
            else:
                await msg.reply_text(get_text("no_channels", lang))
            context.user_data["awaiting_mcq"] = False
            return
        else:
            await msg.reply_text(get_text("no_q", lang))
            context.user_data["awaiting_mcq"] = False
            return
    if chat_type == ChatType.PRIVATE:
        found = await enqueue_mcq(msg, context, is_private=True)
        if not found:
            await context.bot.send_message(msg.chat.id, get_text("no_q", lang))
        return
    content = (msg.text or msg.caption or "").lower()
    bot_username = (await context.bot.get_me()).username.lower()
    if chat_type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        if (
            (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id)
            or content.strip().startswith(f"@{bot_username}")
        ):
            found = await enqueue_mcq(msg, context, is_private=False)
            if not found:
                await context.bot.send_message(msg.chat.id, get_text("no_q", lang))

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle channel posts."""
    post = update.channel_post or update.edited_channel_post
    if not post:
        return
    conn = await get_db()
    try:
        bot_member = await context.bot.get_chat_member(post.chat.id, context.bot.id)
        if bot_member.status not in ["administrator", "creator"] or not bot_member.can_post_messages:
            await context.bot.send_message(post.chat.id, get_text("not_admin", "en"))
            return
    except Exception as e:
        logger.error(f"Failed to verify bot permissions in chat {post.chat.id}: {e}")
        await context.bot.send_message(post.chat.id, get_text("not_admin", "en"))
        return
    await conn.execute(
        "INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)",
        (post.chat.id, post.chat.title or "Untitled"),
    )
    await conn.commit()
    text = (post.text or post.caption or "").strip()
    logger.debug(f"Channel post text in chat {post.chat.id}: {text[:100]}...")
    found = await enqueue_mcq(post, context, is_private=False)
    if not found:
        await context.bot.send_message(post.chat.id, get_text("no_q", "en"))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    lang = context.user_data.get("lang", (update.effective_user.language_code or "en")[:2])
    context.user_data["lang"] = lang
    args = context.args
    if args and args[0].startswith("quiz_"):
        quiz_id = args[0][5:]
        conn = await get_db()
        row = await (await conn.execute("SELECT question, options, correct_option, explanation FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx, expl = row
            opts = opts_str.split(":::")
            send_queues[update.effective_chat.id].append((q, opts, idx, expl))
            asyncio.create_task(process_queue(update.effective_chat.id, context, user_id=update.effective_user.id, is_private=False, quiz_id=quiz_id))
        else:
            await update.message.reply_text(get_text("no_q", lang))
        return
    reply_kb = ReplyKeyboardMarkup(
        [["📝 New Quiz", "🔄 Publish to Channel"], ["📊 My Stats", "📺 Channels"], ["📘 Help"]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
    await update.message.reply_text(
        get_text("start", lang), reply_markup=await build_main_menu(lang), reply_to_message_id=update.message.message_id
    )
    await update.message.reply_text("Choose an option:", reply_markup=reply_kb)

async def set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set default channel for quizzes."""
    lang = context.user_data.get("lang", "en")
    context.user_data["lang"] = lang
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_channels", lang))
        return
    kb = [[InlineKeyboardButton(t, callback_data=f"set_default_{cid}")] for cid, t in rows]
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
    await update.message.reply_text(
        "Choose a default channel:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def repost(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Repost a quiz by ID."""
    lang = context.user_data.get("lang", "en")
    context.user_data["lang"] = lang
    if not context.args:
        await update.message.reply_text("❌ Provide quiz ID. Example: /repost <quiz_id>")
        return
    quiz_id = context.args[0]
    conn = await get_db()
    row = await (await conn.execute("SELECT question, options, correct_option, explanation FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
    if not row:
        await update.message.reply_text(get_text("no_q", lang))
        return
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_channels", lang))
        return
    kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
    await update.message.reply_text(
        "Choose repost destination:", reply_markup=InlineKeyboardMarkup(kb)
    )

async def language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set user language."""
    if not context.args or context.args[0] not in ["en", "ar"]:
        await update.message.reply_text("Usage: /language <en|ar>")
        return
    lang = context.args[0]
    context.user_data["lang"] = lang
    await update.message.reply_text(get_text("language_set", lang, lang=lang.upper()))

async def my_quizzes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List quizzes created by user with pagination."""
    lang = context.user_data.get("lang", "en")
    context.user_data["lang"] = lang
    uid = update.effective_user.id
    conn = await get_db()
    rows = await (await conn.execute("SELECT quiz_id, question FROM quizzes WHERE user_id=?", (uid,))).fetchall()
    if not rows:
        await update.message.reply_text(get_text("no_quizzes", lang))
        return
    page = int(context.args[0]) if context.args and context.args[0].isdigit() else 1
    quizzes_per_page = 5
    total_pages = (len(rows) + quizzes_per_page - 1) // quizzes_per_page
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * quizzes_per_page
    end_idx = start_idx + quizzes_per_page
    quizzes = "\n".join(f"- {q[:50]}... (ID: {qid})" for qid, q in rows[start_idx:end_idx])
    kb = []
    if page > 1:
        kb.append([InlineKeyboardButton("⬅️ Previous", callback_data=f"quizzes_{page-1}")])
    if page < total_pages:
        kb.append([InlineKeyboardButton("Next ➡️", callback_data=f"quizzes_{page+1}")])
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
    await update.message.reply_text(
        get_text("my_quizzes", lang, page=page, total=total_pages, quizzes=quizzes),
        reply_markup=InlineKeyboardMarkup(kb) if kb else None,
    )

async def edit_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Initiate quiz editing."""
    lang = context.user_data.get("lang", "en")
    context.user_data["lang"] = lang
    if not context.args:
        await update.message.reply_text("❌ Provide quiz ID. Example: /editquiz <quiz_id>")
        return
    quiz_id = context.args[0]
    conn = await get_db()
    row = await (await conn.execute("SELECT user_id FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
    if not row or row[0] != update.effective_user.id:
        await update.message.reply_text(get_text("no_quizzes", lang))
        return
    context.user_data["editing_quiz_id"] = quiz_id
    context.user_data["awaiting_mcq"] = True
    await update.message.reply_text(get_text("edit_quiz_prompt", lang, quiz_id=quiz_id))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle callback queries."""
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = context.user_data.get("lang", "en")
    context.user_data["lang"] = lang
    conn = await get_db()
    txt = "⚠️ Unsupported"
    state = "main"
    reply_markup = await build_main_menu(lang, state)
    if cmd == "help":
        txt = get_text("help", lang)
    elif cmd == "new":
        txt = get_text("new", lang)
        context.user_data["awaiting_mcq"] = True
    elif cmd == "stats":
        r = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (uid,))).fetchone()
        s = await (await conn.execute("SELECT SUM(sent) FROM channel_stats")).fetchone()
        txt = get_text("stats", lang, pr=r[0] if r else 0, ch=s[0] if s else 0)
    elif cmd == "channels":
        rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        txt = get_text("no_channels", lang) if not rows else get_text("channels_list", lang, channels="\n".join(f"- {t}: {cid}" for cid, t in rows))
    elif cmd == "publish_channel":
        state = "publish_channel"
        txt = "Choose a channel:"
        reply_markup = await build_main_menu(lang, state)
    elif cmd == "main":
        txt = get_text("start", lang)
        context.user_data["awaiting_mcq"] = False
        context.user_data.pop("editing_quiz_id", None)
    elif cmd.startswith("choose_"):
        cid = int(cmd.split("_")[1])
        row = await (await conn.execute("SELECT title FROM known_channels WHERE chat_id=?", (cid,))).fetchone()
        if row:
            context.chat_data["target_channel"] = cid
            txt = f"✅ Channel selected: {row[0]}.\n" + get_text("private_channel_warning", lang)
        else:
            txt = get_text("no_channels", lang)
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
        row = await (await conn.execute("SELECT question, options, correct_option, explanation FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
            if not rows:
                txt = get_text("no_channels", lang)
            else:
                kb = [[InlineKeyboardButton(t, callback_data=f"repost_to_{quiz_id}_{cid}")] for cid, t in rows]
                kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
                await update.callback_query.edit_message_text(
                    "Choose repost destination:", reply_markup=InlineKeyboardMarkup(kb)
                )
                return
        else:
            txt = get_text("no_q", lang)
    elif cmd.startswith("repost_to_"):
        _, quiz_id, cid = cmd.split("_", 2)
        cid = int(cid)
        row = await (await conn.execute("SELECT question, options, correct_option, explanation FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
 Anchors
        if row:
            q, opts_str, idx, expl = row
            opts = opts_str.split(":::")
            quiz_hash = hashlib.md5((q + ':::'.join(opts)).encode()).hexdigest()
            if quiz_hash in quiz_cache[cid]:
                txt = get_text("no_q", lang)
            else:
                send_queues[cid].append((q, opts, idx, expl))
                asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=False, quiz_id=quiz_id))
                txt = get_text("quiz_sent", lang)
        else:
            txt = get_text("no_q", lang)
    elif cmd.startswith("share_"):
        quiz_id = cmd.split("_")[1]
        rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
        if not rows:
            txt = get_text("no_channels", lang)
        else:
            kb = [[InlineKeyboardButton(t, callback_data=f"share_to_{quiz_id}_{cid}")] for cid, t in rows]
            kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
            txt = "Choose where to share:"
            await update.callback_query.edit_message_text(
                txt, reply_markup=InlineKeyboardMarkup(kb)
            )
            return
    elif cmd.startswith("share_to_"):
        _, quiz_id, cid = cmd.split("_", 2)
        cid = int(cid)
        row = await (await conn.execute("SELECT question, options, correct_option, explanation FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx, expl = row
            opts = opts_str.split(":::")
            quiz_hash = hashlib.md5((q + ':::'.join(opts)).encode()).hexdigest()
            if quiz_hash in quiz_cache[cid]:
                txt = get_text("no_q", lang)
            else:
                send_queues[cid].append((q, opts, idx, expl))
                asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=False, quiz_id=quiz_id))
                txt = get_text("quiz_sent", lang)
        else:
            txt = get_text("no_q", lang)
    elif cmd.startswith("quizzes_"):
        page = int(cmd.split("_")[1])
        rows = await (await conn.execute("SELECT quiz_id, question FROM quizzes WHERE user_id=?", (uid,))).fetchall()
        quizzes_per_page = 5
        total_pages = (len(rows) + quizzes_per_page - 1) // quizzes_per_page
        page = max(1, min(page, total_pages))
        start_idx = (page - 1) * quizzes_per_page
        end_idx = start_idx + quizzes_per_page
        quizzes = "\n".join(f"- {q[:50]}... (ID: {qid})" for qid, q in rows[start_idx:end_idx])
        kb = []
        if page > 1:
            kb.append([InlineKeyboardButton("⬅️ Previous", callback_data=f"quizzes_{page-1}")])
        if page < total_pages:
            kb.append([InlineKeyboardButton("Next ➡️", callback_data=f"quizzes_{page+1}")])
        kb.append([InlineKeyboardButton("❌ Cancel", callback_data="main")])
        txt = get_text("my_quizzes", lang, page=page, total=total_pages, quizzes=quizzes)
        reply_markup = InlineKeyboardMarkup(kb) if kb else None
    elif cmd.startswith("post_to_"):
        cid = int(cmd.split("_")[2])
        row = await (await conn.execute("SELECT title FROM known_channels WHERE chat_id=?", (cid,))).fetchone()
        if row:
            context.chat_data["target_channel"] = cid
            txt = f"✅ Quiz posted to: {row[0]}"
            asyncio.create_task(process_queue(cid, context, user_id=uid, is_private=False))
        else:
            txt = get_text("no_channels", lang)
        context.user_data["awaiting_mcq"] = False
        context.user_data.pop("editing_quiz_id", None)
    await update.callback_query.edit_message_text(txt, reply_markup=reply_markup)

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline queries with spam protection."""
    if not update.inline_query.from_user:
        logger.warning("Blocked anonymous inline query")
        return
    query = update.inline_query.query
    results = []
    if query.startswith("quiz_"):
        quiz_id = query[5:]
        conn = await get_db()
        row = await (await conn.execute("SELECT question, options, correct_option, explanation FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
        if row:
            q, opts_str, idx, expl = row
            opts = opts_str.split(":::")
            content = f"{q}\n" + "\n".join(f"{chr(65+i)}) {opt}" for i, opt in enumerate(opts)) + f"\nAnswer: {chr(65+idx)}"
            if expl:
                content += f"\n\n**Explanation**: {expl}"
            results.append(
                InlineQueryResultArticle(
                    id=quiz_id,
                    title=f"Quiz: {q[:50]}...",
                    input_message_content=InputTextMessageContent(content, parse_mode="MarkdownV2"),
                )
            )
    elif query:
        results.append(
            InlineQueryResultArticle(
                id="1",
                title="Convert to MCQ",
                input_message_content=InputTextMessageContent(query, parse_mode="MarkdownV2"),
            )
        )
    if len(results) > 5:
        results = results[:5]
        logger.warning(f"Limited inline query results for user {update.inline_query.from_user.id}")
    await update.inline_query.answer(results)

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /channels command."""
    lang = context.user_data.get("lang", "en")
    context.user_data["lang"] = lang
    conn = await get_db()
    rows = await (await conn.execute("SELECT chat_id, title FROM known_channels")).fetchall()
    txt = get_text("no_channels", lang) if not rows else get_text("channels_list", lang, channels="\n".join(f"- {t}: {cid}" for cid, t in rows))
    await update.message.reply_text(txt)

def main() -> None:
    """Main entry point."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("❌ Bot token not found. Set TELEGRAM_BOT_TOKEN.")
    
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("channels", channels_command))
    app.add_handler(CommandHandler("setchannel", set_channel))
    app.add_handler(CommandHandler("repost", repost))
    app.add_handler(CommandHandler("language", language))
    app.add_handler(CommandHandler("myquizzes", my_quizzes))
    app.add_handler(CommandHandler("editquiz", edit_quiz))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(
        MessageHandler(
            filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption),
            handle_channel_post,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def startup(application):
        conn = await get_db()
        await init_db(conn)
        asyncio.create_task(schedule_cleanup())

    app.post_init = startup

    async def shutdown(application):
        await close_db()

    app.post_shutdown = shutdown

    logger.info("✅ Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
