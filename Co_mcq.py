import os
import re
import logging
import asyncio
import hashlib
import aiosqlite
import time
from collections import defaultdict
from telegram import (
    Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    InlineQueryHandler, filters, ContextTypes
)

# ===== إعداد سجل الأخطاء مع مستوى DEBUG + تسجيل في ملف =====
logging.basicConfig(
    filename='bot_errors.log',
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# ===== متغيرات منع سبام وإدارة الطوابير =====
send_queues = defaultdict(asyncio.Queue)          # طابور إرسال لكل دردشة
last_sent_time_user = defaultdict(float)           # لمنع سبام في الخاص
last_sent_time_chat = defaultdict(float)           # لمنع سبام في المجموعات والقنوات
send_locks = defaultdict(asyncio.Lock)             # أقفال لمنع تداخل الإرسال
queue_tasks = {}                                    # تتبع حالة مهمة إرسال الطوابير per chat

# ===== أقفال لمنع تداخل تحديث أوقات الإرسال =====
user_time_lock = asyncio.Lock()
chat_time_lock = asyncio.Lock()

# ===== دعم الحروف العربية والأرقام =====
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4'}
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3}

# ===== أنماط التعبير النمطي لتحليل أسئلة MCQ =====
PATTERNS = [
    re.compile(r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-D][).:]\s*.+?\s*){2,10})(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Da-d1-4١-٤])", re.S | re.IGNORECASE),
    re.compile(r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-د][).:]\s*.+?\s*){2,10})الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-د1-4١-٤])", re.S),
    re.compile(r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9]+[).:]\s*.+?\n){2,10})(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9١-٤])", re.S | re.IGNORECASE)
]

# ===== نصوص متعددة اللغات =====
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

def get_text(key: str, lang: str) -> str:
    """إرجاع النص المناسب حسب اللغة مع fallback للإنجليزية"""
    return TEXTS.get(key, {}).get(lang, TEXTS.get(key, {}).get('en', ''))

def get_lang(obj) -> str:
    """إرجاع رمز اللغة (en/ar) مع التحقق الآمن"""
    lang = getattr(obj, 'language_code', None)
    if lang and len(lang) >= 2:
        return lang[:2].lower()
    return 'en'

def hash_question(text: str) -> str:
    """هاش ثابت للسؤال لتفادي التكرار مع تنسيق النص"""
    normalized = re.sub(r'\s+', ' ', text.lower().strip())
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()

async def init_db():
    """تهيئة وإنشاء الجداول في قاعدة البيانات"""
    async with aiosqlite.connect('stats.db') as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                sent INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS channel_stats (
                chat_id INTEGER PRIMARY KEY,
                sent INTEGER DEFAULT 0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sent_questions (
                chat_id INTEGER,
                hash TEXT,
                PRIMARY KEY(chat_id, hash)
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS known_channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        ''')
        await db.commit()

def split_options(opts_raw: str) -> list:
    """
    تحسين استخراج الخيارات من نص خام (multiline string)
    يحاول استخراج نص الخيار بعد الحرف والرمز مثل A), B., أ)، ... الخ.
    """
    opts = []
    lines = opts_raw.strip().splitlines()
    option_pattern = re.compile(r'^[A-Za-zأ-د][).:]\s*(.+)$')
    for line in lines:
        line = line.strip()
        m = option_pattern.match(line)
        if m:
            opts.append(m.group(1).strip())
    return opts

async def parse_mcq(text: str, chat_id: int) -> list:
    """
    تحليل نص السؤال MCQ واستخراج السؤال والخيارات والإجابة مع تخزين الهاش لمنع التكرار.
    يعيد قائمة tuples من (question, options, correct_index)
    """
    res = []
    new_hashes = []
    async with aiosqlite.connect('stats.db') as db:
        for patt in PATTERNS:
            for m in patt.finditer(text):
                q = m.group('q').strip()
                h = hash_question(q)
                async with db.execute('SELECT 1 FROM sent_questions WHERE chat_id=? AND hash=?', (chat_id, h)) as cursor:
                    found = await cursor.fetchone()
                if found:
                    continue

                opts_raw = m.group('opts')
                opts = split_options(opts_raw)
                if not (2 <= len(opts) <= 10):
                    continue

                raw_ans = m.group('ans').strip()
                ans = ARABIC_DIGITS.get(raw_ans, raw_ans)
                idx = None
                try:
                    if ans.isdigit():
                        idx = int(ans) - 1
                    elif ans.lower() in 'abcd':
                        idx = ord(ans.lower()) - ord('a')
                    else:
                        idx = AR_LETTERS.get(raw_ans)
                except Exception:
                    idx = None

                if idx is None or not (0 <= idx < len(opts)):
                    continue

                res.append((q, opts, idx))
                new_hashes.append((chat_id, h))

        if new_hashes:
            try:
                await db.executemany('INSERT INTO sent_questions(chat_id, hash) VALUES (?, ?)', new_hashes)
                await db.commit()
            except Exception as e:
                logger.error(f"DB Insert error in parse_mcq: {e}")
    return res

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """معالجة إرسال أسئلة الطابور واحدة تلو الأخرى مع منع التداخل"""
    async with send_locks[chat_id]:
        queue = send_queues[chat_id]
        while not queue.empty():
            qst, opts, idx = await queue.get()
            try:
                await context.bot.send_poll(
                    chat_id,
                    qst,
                    opts,
                    type=Poll.QUIZ,
                    correct_option_id=idx,
                    is_anonymous=False
                )
                await asyncio.sleep(0.5)  # لتخفيف الحمل ومنع الحظر
            except Exception as e:
                logger.error(f"Failed to send poll to {chat_id}: {e}")
                try:
                    await context.bot.send_message(chat_id, get_text('error_poll', 'ar'))
                except Exception as e2:
                    logger.error(f"Failed to send error message after poll failure: {e2}")
                break
    queue_tasks.pop(chat_id, None)  # تحرير المهمة من التتبع

async def enqueue_mcq(message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    تحليل رسالة المستخدم إلى أسئلة MCQ ووضعها في طابور الإرسال.
    يعيد True إذا تم وضع أسئلة للطابور، False إذا لم يتم التعرف على أسئلة.
    """
    chat = message.chat
    chat_id = chat.id

    # تسجيل القنوات المعروفة
    if chat.type == 'channel':
        title = chat.title or 'Private Channel'
        try:
            async with aiosqlite.connect('stats.db') as db:
                await db.execute('INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)', (chat_id, title))
                await db.commit()
        except Exception as e:
            logger.error(f"DB Insert error in enqueue_mcq (known_channels): {e}")

    # الحد الأقصى لطول الطابور
    if send_queues[chat_id].qsize() > 50:
        lang = get_lang(message.from_user)
        await context.bot.send_message(chat_id, get_text('queue_full', lang))
        return False

    text = message.text or message.caption or ''
    if not text.strip():
        return False

    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    sent = False
    for blk in blocks:
        lst = await parse_mcq(blk, chat_id)
        for item in lst:
            await send_queues[chat_id].put(item)
            sent = True

    if sent:
        # إطلاق مهمة معالجة الطابور إذا لم تكن تعمل بالفعل
        if chat_id not in queue_tasks:
            queue_tasks[chat_id] = asyncio.create_task(process_queue(chat_id, context))

    return sent

async def update_last_sent_time(user_or_chat: int, is_user: bool):
    """تحديث الوقت مع أقفال لتجنب مشاكل التزامن"""
    now = time.time()
    if is_user:
        async with user_time_lock:
            last_sent_time_user[user_or_chat] = now
    else:
        async with chat_time_lock:
            last_sent_time_chat[user_or_chat] = now

async def can_send(user_or_chat: int, is_user: bool) -> bool:
    """التحقق من إمكانية الإرسال حسب الوقت المنقضي مع أقفال"""
    now = time.time()
    if is_user:
        async with user_time_lock:
            last = last_sent_time_user.get(user_or_chat, 0)
        return now - last >= 5
    else:
        async with chat_time_lock:
            last = last_sent_time_chat.get(user_or_chat, 0)
        return now - last >= 3

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التعامل مع الرسائل النصية وتحويلها لأسئلة"""
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return

    if not msg.from_user:
        return

    uid = msg.from_user.id
    ct = msg.chat.type
    lang = get_lang(msg.from_user)

    # منع سبام خاص ومجموعات
    if ct == 'private':
        if not await can_send(uid, True):
            return
        await update_last_sent_time(uid, True)
    else:
        if not await can_send(msg.chat.id, False):
            return
        await update_last_sent_time(msg.chat.id, False)

    if ct == 'private':
        if await enqueue_mcq(msg, context):
            try:
                async with aiosqlite.connect('stats.db') as db:
                    await db.execute('INSERT OR IGNORE INTO user_stats VALUES (?, 0)', (uid,))
                    await db.execute('UPDATE user_stats SET sent=sent+1 WHERE user_id=?', (uid,))
                    await db.commit()
            except Exception as e:
                logger.error(f"DB Update error in handle_text private: {e}")

            try:
                await msg.delete()
            except Exception as e:
                logger.warning(f"Failed to delete message: {e}")
        else:
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))
        return

    # في مجموعات وقنوات: يجب ذكر البوت أو الرد عليه
    bot_username = (context.bot.username or '').lower()
    text_lower = (msg.text or msg.caption or '').lower()
    is_mention = f"@{bot_username}" in text_lower if bot_username else False
    is_reply_to_bot = msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.id == context.bot.id

    if ct in ['group', 'supergroup', 'channel']:
        if not (is_mention or is_reply_to_bot):
            return

        if ct == 'channel':
            title = msg.chat.title or 'Private Channel'
            try:
                async with aiosqlite.connect('stats.db') as db:
                    await db.execute('INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)', (msg.chat.id, title))
                    await db.commit()
            except Exception as e:
                logger.error(f"DB Insert error in handle_text channel: {e}")

        if await enqueue_mcq(msg, context):
            try:
                async with aiosqlite.connect('stats.db') as db:
                    await db.execute('INSERT OR IGNORE INTO channel_stats VALUES (?, 0)', (msg.chat.id,))
                    await db.execute('UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?', (msg.chat.id,))
                    await db.commit()
            except Exception as e:
                logger.error(f"DB Update error in handle_text channel/group: {e}")
        else:
            await context.bot.send_message(msg.chat.id, get_text('invalid_format', lang))

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التعامل مع منشورات القنوات"""
    post = update.channel_post
    if not post:
        return

    try:
        async with aiosqlite.connect('stats.db') as db:
            await db.execute('INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)', (post.chat.id, post.chat.title or 'Private Channel'))
            await db.commit()
    except Exception as e:
        logger.error(f"DB Insert error in handle_channel_post: {e}")

    if await enqueue_mcq(post, context):
        try:
            async with aiosqlite.connect('stats.db') as db:
                await db.execute('INSERT OR IGNORE INTO channel_stats VALUES (?, 0)', (post.chat.id,))
                await db.execute('UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?', (post.chat.id,))
                await db.commit()
        except Exception as e:
            logger.error(f"DB Update error in handle_channel_post: {e}")

async def channel_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إظهار معرف القناة"""
    chat = update.effective_chat
    if chat.type == 'channel':
        await update.message.reply_text(f"📡 معرف هذه القناة هو: `{chat.id}`", parse_mode='Markdown')
    else:
        await update.message.reply_text("⚠️ هذا الأمر يعمل فقط داخل القنوات.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة /start مع أزرار تفاعلية"""
    lang = get_lang(update.effective_user)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')],
        [InlineKeyboardButton('📺 القنوات', callback_data='channels')]
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التعامل مع أزرار القائمة التفاعلية"""
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = get_lang(update.effective_user)

    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        txt = get_text('new', lang)
    elif cmd == 'stats':
        try:
            async with aiosqlite.connect('stats.db') as db:
                async with db.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,)) as cursor:
                    r = await cursor.fetchone()
                    sent = r[0] if r else 0
                async with db.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,)) as cursor:
                    r = await cursor.fetchone()
                    ch = r[0] if r else 0
        except Exception as e:
            logger.error(f"DB error in callback stats: {e}")
            sent, ch = 0, 0
        txt = get_text('stats', lang).format(sent=sent, ch=ch)
    elif cmd == 'channels':
        try:
            async with aiosqlite.connect('stats.db') as db:
                async with db.execute('SELECT chat_id, title FROM known_channels') as cursor:
                    rows = await cursor.fetchall()
            if not rows:
                txt = "لا توجد قنوات معروفة حالياً."
            else:
                txt = "📢 القنوات المعروفة:\n" + "\n".join(f"{cid}: {title}" for cid, title in rows)
        except Exception as e:
            logger.error(f"DB error in callback channels: {e}")
            txt = "⚠️ خطأ في جلب بيانات القنوات."
    else:
        txt = "❓ خيار غير معروف."

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(txt)

async def main():
    # تهيئة DB
    await init_db()

    TOKEN = os.getenv("TOKEN")
    if not TOKEN:
        logger.error("Error: TOKEN environment variable not set.")
        return

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("channel_id", channel_id_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text))
    application.add_handler(MessageHandler(filters.StatusUpdate.CHANNEL_CHAT_CREATED, handle_channel_post))
    application.add_handler(CallbackQueryHandler(callback_query_handler))

    logger.info("Bot started polling...")
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
 
