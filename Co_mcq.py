import os
import re
import logging
import asyncio
import time
from collections import defaultdict, deque

import aiosqlite
from telegram import (
    Update, Poll,
    InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler,
    filters, ContextTypes
)

# === Logger ===
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = "stats.db"
MAX_QUEUE = 50
RATE_LIMIT_SECONDS = 5
MAX_OPTIONS = 10

send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)
db_lock = asyncio.Lock()

ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9', '٠': '0'}
AR_LETTERS = {
    'أ': 0, 'ب': 1, 'ج': 2, 'د': 3, 'هـ': 4, 'و': 5, 'ز': 6, 'ح': 7, 'ط': 8, 'ي': 9,
    'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7, 'I': 8, 'J': 9,
    '1': 0, '2': 1, '3': 2, '4': 3, '5': 4, '6': 5, '7': 6, '8': 7, '9': 8, '0': 9
}

PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-Jأ-ي0-9][).:،]\s*.+?\s*){2,10})"
        r"(?:Answer|Ans|Correct Answer|الإجابة\s+الصحيحة)[:：]?[ \n\t]*?(?P<ans>[A-Ja-jأ-ي0-9١-٩])",
        re.S | re.IGNORECASE
    )
]

TEXTS = {
    'no_q': {
        'en': '❌ No valid MCQ detected.',
        'ar': '❌ لم يتم التعرف على سؤال متعدد الخيارات بصيغة صحيحة.'
    },
    'queue_full': {
        'en': '🚫 Queue full. Please wait.',
        'ar': '🚫 قائمة الانتظار ممتلئة، الرجاء الانتظار.'
    }
}

def get_lang(update: Update) -> str:
    return (update.effective_user.language_code or 'en')[:2]

def get_text(key: str, lang: str = 'en') -> str:
    return TEXTS.get(key, {}).get(lang, TEXTS[key]['en'])

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS user_stats (
            user_id INTEGER PRIMARY KEY,
            sent INTEGER DEFAULT 0
        )
        """)
        await db.commit()

# ✅ MCQ Parsing with up to 10 options
def parse_mcq(text: str) -> list[tuple[str, list[str], int]]:
    results = []
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            try:
                q = match.group('q').strip()
                opts_raw = match.group('opts')
                ans_raw = match.group('ans').strip()

                opts = re.findall(r'[A-Jأ-ي0-9][).:،]\s*(.+)', opts_raw)
                if len(opts) < 2 or len(opts) > MAX_OPTIONS:
                    continue

                ans_raw = ARABIC_DIGITS.get(ans_raw, ans_raw.upper())
                idx = AR_LETTERS.get(ans_raw, None)
                if idx is None:
                    idx = int(ans_raw) - 1 if ans_raw.isdigit() else None
                if idx is not None and 0 <= idx < len(opts):
                    results.append((q, opts, idx))
            except Exception as e:
                logger.warning(f"Parsing error: {e}")
                continue
    return results

# ✅ Rate-limited Poll sending
async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    queue = send_queues[chat_id]
    while queue:
        q, opts, idx = queue.popleft()
        now = time.time()
        if now - last_sent_time[chat_id] < RATE_LIMIT_SECONDS:
            await asyncio.sleep(RATE_LIMIT_SECONDS - (now - last_sent_time[chat_id]))
        last_sent_time[chat_id] = time.time()
        try:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=q,
                options=opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False
            )
        except Exception as e:
            logger.error(f"Poll send error to {chat_id}: {e}")

# ✅ Add to queue from message
async def enqueue_mcq(msg, context):
    chat_id = msg.chat.id
    if len(send_queues[chat_id]) >= MAX_QUEUE:
        lang = get_lang(msg)
        if msg.chat.type != 'channel':
            await context.bot.send_message(chat_id, get_text('queue_full', lang))
        return False

    text = msg.text or msg.caption or ''
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    sent_any = False
    for blk in blocks:
        for q, opts, idx in parse_mcq(blk):
            send_queues[chat_id].append((q, opts, idx))
            sent_any = True

    if sent_any:
        context.application.create_task(process_queue(chat_id, context))
    else:
        lang = get_lang(msg)
        if msg.chat.type != 'channel':
            await context.bot.send_message(chat_id, get_text('no_q', lang))
    return sent_any

# ✅ Handlers
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return
    if await enqueue_mcq(msg, context):
        uid = msg.from_user.id
        async with db_lock, aiosqlite.connect(DB_PATH, timeout=10) as db:
            await db.execute('INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES(?, 0)', (uid,))
            await db.execute('UPDATE user_stats SET sent = sent + 1 WHERE user_id=?', (uid,))
            await db.commit()
        try:
            await msg.delete()
        except:
            pass

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    bot_username = context.bot.username.lower()
    content = (post.text or post.caption or '').lower()
    if f"@{bot_username}" in content:
        await enqueue_mcq(post, context)

# ✅ Inline Mode Stub
async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip()
    if not query:
        return
    result = InlineQueryResultArticle(
        id='1',
        title='📄 تحويل سؤال',
        input_message_content=InputTextMessageContent(query)
    )
    await update.inline_query.answer([result])

# ✅ Bot entry
async def main():
    await init_db()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    app = Application.builder().token(token).build()

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL, handle_channel_post))
    app.add_handler(InlineQueryHandler(inline_query))

    logger.info("✅ Bot is running...")
    await app.run_polling()

# ✅ Safe async entry point
if __name__ == '__main__':
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "already running" in str(e):
            loop = asyncio.get_event_loop()
            loop.create_task(main())
            loop.run_forever()
        else:
            raise
