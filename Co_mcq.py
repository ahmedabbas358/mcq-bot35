import os
import re
import logging
import asyncio
import hashlib
import aiosqlite
from collections import defaultdict
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ===== Configuration from environment =====
TOKEN = os.getenv('TOKEN')
DB_PATH = os.getenv('DB_PATH', 'stats.db')
QUEUE_MAX_SIZE = int(os.getenv('QUEUE_MAX_SIZE', 50))
USER_RATE_LIMIT = float(os.getenv('USER_RATE_LIMIT', 5.0))    # seconds
CHAT_RATE_LIMIT = float(os.getenv('CHAT_RATE_LIMIT', 3.0))    # seconds

# ===== Logging setup =====
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

fh = logging.FileHandler('bot_errors.log')
fh.setLevel(logging.ERROR)
fh.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(fh)

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(ch)

# ===== Globals =====
send_queues = defaultdict(asyncio.Queue)
send_locks = defaultdict(asyncio.Lock)
processing_chats = set()
last_sent = {}
rate_locks = defaultdict(asyncio.Lock)

# ===== Patterns & Texts =====
PATTERNS = [
    re.compile(r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-D][).:]\s*.+?\s*){2,10})(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Da-d1-4١-٤])", re.S | re.IGNORECASE),
    re.compile(r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-د][).:]\s*.+?\s*){2,10})الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-د1-4١-٤])", re.S),
    re.compile(r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9]+[).:]\s*.+?\n){2,10})(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9١-٤])", re.S | re.IGNORECASE)
]

TEXTS = {
    'start': {'en': '🤖 Hi! Choose an option:', 'ar': '🤖 أهلاً! اختر من القائمة:'},
    'help': {'en': 'Usage:\n- Send MCQ in private.\n- Mention or reply in groups.\n- Formats: Q:/س:', 'ar': '🆘 كيفية الاستخدام:\n- في الخاص أرسل السؤال.\n- في المجموعات اذكر @البوت أو الرد.\n- الصيغ: Q:/س:'},
    'new': {'en': '📩 Send your MCQ now!', 'ar': '📩 أرسل سؤال MCQ الآن!'},
    'stats': {'en': '📊 You sent {sent} questions.\n✉️ Channel posts: {ch}', 'ar': '📊 أرسلت {sent} سؤالاً.\n🏷️ منشورات القناة: {ch}'},
    'queue_full': {'en': '🚫 Queue full, send fewer questions.', 'ar': '🚫 القائمة ممتلئة، أرسل أقل.'},
    'no_q': {'en': '❌ No questions detected.', 'ar': '❌ لم أتعرف على أي سؤال.'},
    'error_poll': {'en': '⚠️ Failed to send question.', 'ar': '⚠️ فشل في إرسال السؤال.'},
    'invalid_format': {'en': '⚠️ Please send a properly formatted MCQ.', 'ar': '⚠️ الرجاء إرسال السؤال بصيغة صحيحة.'}
}

ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4'}
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3}

# ===== Utility Functions =====
def get_text(key: str, lang: str) -> str:
    return TEXTS.get(key, {}).get(lang, TEXTS[key]['en'])

def get_lang(user) -> str:
    if user:
        code = getattr(user, 'language_code', 'en')
        return code[:2].lower() if code else 'en'
    return 'en'

def hash_question(text: str) -> str:
    norm = re.sub(r'\s+', ' ', text.lower().strip())
    return hashlib.sha256(norm.encode()).hexdigest()

def split_options(opts_raw: str) -> list:
    opts = []
    pattern = re.compile(r'^[A-Za-zأ-د][).:]\s*(.+)$')
    for line in opts_raw.splitlines():
        m = pattern.match(line.strip())
        if m:
            opts.append(m.group(1).strip())
    return opts

async def init_db(db):
    await db.executescript('''
        CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS sent_questions (chat_id INTEGER, hash TEXT, PRIMARY KEY(chat_id, hash));
        CREATE INDEX IF NOT EXISTS idx_sent_questions_chat_hash ON sent_questions(chat_id, hash);
        CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT);
    ''')
    await db.commit()

# ===== Rate Limiter =====
async def can_send(key: str, limit: float) -> bool:
    async with rate_locks[key]:
        now = asyncio.get_event_loop().time()
        last = last_sent.get(key, 0)
        if now - last >= limit:
            last_sent[key] = now
            return True
        return False

# ===== MCQ Parsing =====
async def parse_mcq(text: str, chat_id: int, db) -> list:
    results, hashes = [], []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            h = hash_question(q)
            cur = await db.execute('SELECT 1 FROM sent_questions WHERE chat_id=? AND hash=?', (chat_id, h))
            if await cur.fetchone():
                continue
            opts = split_options(m.group('opts'))
            if not 2 <= len(opts) <= 10:
                continue
            raw = m.group('ans').strip()
            raw = ARABIC_DIGITS.get(raw, raw)
            idx = None
            if raw.isdigit(): idx = int(raw) - 1
            elif raw.lower() in 'abcd': idx = ord(raw.lower()) - ord('a')
            else: idx = AR_LETTERS.get(raw)
            if idx is None or not (0 <= idx < len(opts)):
                continue
            results.append((q, opts, idx)); hashes.append((chat_id, h))
    if hashes:
        await db.executemany('INSERT INTO sent_questions(chat_id,hash) VALUES (?,?)', hashes)
        await db.commit()
    return results

# ===== Queue Processing =====
async def process_queue(chat_id: int, application):
    db = application.bot_data['db']
    try:
        async with send_locks[chat_id]:
            queue = send_queues[chat_id]
            while not queue.empty():
                q, opts, idx, user_id = await queue.get()
                try:
                    await application.bot.send_poll(
                        chat_id, q, opts,
                        type=Poll.QUIZ,
                        correct_option_id=idx,
                        is_anonymous=False
                    )
                    # Update user stats
                    await db.execute('INSERT OR IGNORE INTO user_stats(user_id) VALUES (?)', (user_id,))
                    await db.execute('UPDATE user_stats SET sent = sent + 1 WHERE user_id=?', (user_id,))
                    # Update channel/group stats
                    await db.execute('INSERT OR IGNORE INTO channel_stats(chat_id) VALUES (?)', (chat_id,))
                    await db.execute('UPDATE channel_stats SET sent = sent + 1 WHERE chat_id=?', (chat_id,))
                    await db.commit()
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"Failed to send poll to {chat_id}: {e}")
                    try:
                        await application.bot.send_message(chat_id, get_text('error_poll', 'ar'))
                    except:
                        pass
                    break
    finally:
        processing_chats.discard(chat_id)
        send_queues.pop(chat_id, None)

async def enqueue_mcq(msg, application, db, lang) -> bool:
    chat_id = msg.chat.id
    user_id = msg.from_user.id if msg.from_user else chat_id
    key_user = f"user:{user_id}"
    key_chat = f"chat:{chat_id}"
    if send_queues[chat_id].qsize() >= QUEUE_MAX_SIZE:
        await application.bot.send_message(chat_id, get_text('queue_full', lang))
        return False
    text = msg.text or msg.caption or ''
    sent = False
    for block in re.split(r"\n{2,}", text):
        for q, opts, idx in await parse_mcq(block, chat_id, db):
            await send_queues[chat_id].put((q, opts, idx, user_id))
            sent = True
    if sent and chat_id not in processing_chats:
        processing_chats.add(chat_id)
        asyncio.create_task(process_queue(chat_id, application))
    return sent

# ===== Handlers =====
async def handle_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.channel_post
    if not msg or not (msg.text or msg.caption):
        return
    chat = msg.chat
    user_id = msg.from_user.id if msg.from_user else None
    key = f"user:{user_id}" if chat.type == 'private' else f"chat:{chat.id}"
    limit = USER_RATE_LIMIT if chat.type == 'private' else CHAT_RATE_LIMIT
    if not await can_send(key, limit):
        return
    db = context.bot_data['db']
    lang = get_lang(msg.from_user)

    if chat.type == 'private':
        success = await enqueue_mcq(msg, context.application, db, lang)
        if not success:
            await msg.reply_text(get_text('no_q', lang))
        else:
            try:
                await msg.delete()
            except Exception as e:
                logger.warning(f"Could not delete message: {e}")
        return

    if chat.type in ['group', 'supergroup']:
        bot_un = context.bot.username.lower()
        text = (msg.text or msg.caption).lower()
        if f"@{bot_un}" not in text and not (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id):
            return
        if not await enqueue_mcq(msg, context.application, db, lang):
            await msg.reply_text(get_text('invalid_format', lang))
        return

    if chat.type == 'channel':
        await db.execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES (?,?)',
                         (chat.id, chat.title or ''))
        await db.commit()
        if not await enqueue_mcq(msg, context.application, db, lang):
            await context.bot.send_message(chat.id, get_text('invalid_format', lang))
        return

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    lang = get_lang(user)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')],
        [InlineKeyboardButton('📺 القنوات', callback_data='channels')]
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def callback_q(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = context.bot_data['db']
    cmd = update.callback_query.data
    chat_id = update.effective_chat.id
    uid = update.effective_user.id
    lang = get_lang(update.effective_user)
    text = ''
    try:
        if cmd == 'help':
            text = get_text('help', lang)
        elif cmd == 'new':
            text = get_text('new', lang)
        elif cmd == 'stats':
            row = await db.execute_fetchone('SELECT sent FROM user_stats WHERE user_id=?', (uid,))
            user_sent = row[0] if row else 0
            row = await db.execute_fetchone('SELECT sent FROM channel_stats WHERE chat_id=?', (chat_id,))
            ch_sent = row[0] if row else 0
            text = get_text('stats', lang).format(sent=user_sent, ch=ch_sent)
        elif cmd == 'channels':
            rows = await db.execute_fetchall('SELECT chat_id, title FROM known_channels')
            text = 'لا توجد قنوات حالياً.' if not rows else '📢 Known channels:\n' + '\n'.join(f"{cid}: {title}" for cid, title in rows)
        else:
            text = '❓ خيار غير معروف.'
    except Exception as e:
        logger.error(f"Callback DB error: {e}")
        text = '⚠️ خطأ في تنفيذ الأمر.'
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(text)

# ===== Main =====
async def main():
    if not TOKEN:
        logger.error('TOKEN not set in environment')
        return
    db = await aiosqlite.connect(DB_PATH)
    await init_db(db)

    app = Application.builder().token(TOKEN).build()
    app.bot_data['db'] = db

    app.add_handler(CommandHandler('start', start_cmd))
    app.add_handler(CallbackQueryHandler(callback_q))
    app.add_handler(MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.StatusUpdate.CHANNEL_POST, handle_all))

    logger.info('Bot is starting...')
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
