# co_mcq_bot.py - النسخة الشاملة المحسنة باستخدام aiosqlite وجاهزة للـ Railway
import os
import re
import logging
import asyncio
import time
from collections import defaultdict, deque
from typing import Deque, Tuple, List, Optional

import aiosqlite
from telegram import (
    Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent, Message
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    InlineQueryHandler, filters, ContextTypes
)

# إعداد السجل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# متغيرات البيئة
DATABASE = os.getenv('DATABASE_URL', 'stats.db')
SEND_DELAY_SECONDS: float = 0.5  # تأخير بين كل سؤال وآخر

# خريطة الأرقام والحروف العربية
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4'}
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3}

# أنماط تحليل MCQ
PATTERNS = [
    re.compile(r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-D][).:]\s*.+?\s*){2,10})"
               r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Da-d1-4١-٤])",
               re.S | re.IGNORECASE),
    re.compile(r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-د][).:]\s*.+?\s*){2,10})"
               r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-د1-4١-٤])",
               re.S),
    re.compile(r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9]+[).:]\s*.+?\n){2,10})"
               r"(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9١-٤])",
               re.S | re.IGNORECASE),
]

# نصوص متعددة اللغات
TEXTS = {
    'start':    {'en':'🤖 Hi! Choose an option:','ar':'🤖 أهلاً! اختر من القائمة:'},
    'help':     {'en':'Usage:\n- Send MCQ in private.\n- Mention or reply in groups.\n- Formats: Q:/س:',
                 'ar':'🆘 كيفية الاستخدام:\n- في الخاص أرسل السؤال.\n- في المجموعات اذكر @البوت أو الرد.\n- الصيغ: Q:/س:'},
    'new':      {'en':'📩 Send your MCQ now!','ar':'📩 أرسل سؤال MCQ الآن!'},
    'stats':    {'en':'📊 You sent {sent} questions.\n✉️ Channel posts: {ch}',
                 'ar':'📊 أرسلت {sent} سؤالاً.\n🏷️ منشورات القناة: {ch}'},
    'queue_full':{'en':'🚫 Queue full, send fewer questions.','ar':'🚫 القائمة ممتلئة، أرسل أقل.'},
    'no_q':     {'en':'❌ No questions detected.','ar':'❌ لم أتعرف على أي سؤال.'},
    'error_poll':{'en':'⚠️ Failed to send question.','ar':'⚠️ فشل في إرسال السؤال.'}
}

def get_text(key: str, lang: str) -> str:
    return TEXTS[key].get(lang, TEXTS[key]['en'])

def get_lang(entity) -> str:
    code = getattr(entity, 'language_code', None) or (
        entity.from_user.language_code if hasattr(entity, 'from_user') and entity.from_user else None
    )
    return (code or 'en')[:2]

# قائمة الانتظار وقفل معالجة لكل دردشة
send_queues: defaultdict[int, Deque[Tuple[str, List[str], int]]] = defaultdict(deque)
queue_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
last_sent_time: defaultdict[int, float] = defaultdict(float)

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                sent INTEGER DEFAULT 0
            )''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS channel_stats (
                chat_id INTEGER PRIMARY KEY,
                sent INTEGER DEFAULT 0
            )''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS sent_questions (
                chat_id INTEGER,
                hash INTEGER,
                timestamp INTEGER DEFAULT (strftime('%s','now')),
                PRIMARY KEY(chat_id, hash)
            )''')
        await db.commit()

async def parse_mcq(text: str, chat_id: int, db) -> List[Tuple[str, List[str], int]]:
    results = []
    inserts: List[Tuple[int,int]] = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            h = hash(q)
            async with db.execute('SELECT 1 FROM sent_questions WHERE chat_id=? AND hash=?', (chat_id, h)) as cur:
                if await cur.fetchone():
                    continue
            inserts.append((chat_id, h))
            opts = []
            for ln in m.group('opts').strip().splitlines():
                parts = re.split(r'^[A-Za-zء-ي١-٩0-9][).:]\s*', ln.strip(), 1)
                if len(parts) == 2:
                    opts.append(parts[1].strip())
            if not 2 <= len(opts) <= 10:
                continue
            raw = m.group('ans').strip()
            ans = ARABIC_DIGITS.get(raw, raw)
            idx: Optional[int] = None
            try:
                if ans.isdigit():
                    idx = int(ans) - 1
                elif ans.lower() in 'abcd':
                    idx = ord(ans.lower()) - ord('a')
                else:
                    idx = AR_LETTERS.get(raw)
            except:
                continue
            if idx is None or not 0 <= idx < len(opts):
                continue
            results.append((q, opts, idx))
    if inserts:
        await db.executemany('INSERT INTO sent_questions(chat_id,hash) VALUES(?,?)', inserts)
        await db.commit()
    return results

async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ضمان عدم تزامن إرسال قائمة الانتظار لنفس الدردشة
    async with queue_locks[chat_id]:
        queue = send_queues[chat_id]
        while queue:
            qst, opts, idx = queue.popleft()
            try:
                await context.bot.send_poll(
                    chat_id, qst, opts,
                    type=Poll.QUIZ,
                    correct_option_id=idx,
                    is_anonymous=False
                )
                await asyncio.sleep(SEND_DELAY_SECONDS)
            except Exception as e:
                logger.error(f"Failed to send poll: {e}")
                lang = 'en'
                await context.bot.send_message(chat_id, get_text('error_poll', lang))
                break

async def enqueue_mcq(message: Message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat_id = message.chat.id
    lang = get_lang(message)
    if len(send_queues[chat_id]) > 50:
        await context.bot.send_message(chat_id, get_text('queue_full', lang))
        return False
    text_content = message.text or message.caption or ''
    blocks = [b.strip() for b in re.split(r"\n{2,}", text_content) if b.strip()]
    sent = False
    async with aiosqlite.connect(DATABASE) as db:
        for blk in blocks:
            items = await parse_mcq(blk, chat_id, db)
            for itm in items:
                send_queues[chat_id].append(itm)
                sent = True
    if sent:
        # ابدأ مهمة المعالجة إذا لم تكن قيد التشغيل
        asyncio.create_task(process_queue(chat_id, context))
    return sent

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return
    uid = msg.from_user.id
    now = time.time()
    if now - last_sent_time[uid] < 5:
        return
    last_sent_time[uid] = now
    ct = msg.chat.type
    lang = get_lang(msg)
    # دردشة خاصة
    if ct == 'private':
        sent = await enqueue_mcq(msg, context)
        if sent:
            async with aiosqlite.connect(DATABASE) as db:
                await db.execute('INSERT OR IGNORE INTO user_stats VALUES(?,0)', (uid,))
                await db.execute(
                    'UPDATE user_stats SET sent=sent+? WHERE user_id=?',
                    (len(send_queues[msg.chat.id]), uid)
                )
                await db.commit()
            try:
                await msg.delete()
            except Exception:
                logger.warning("Failed to delete user message")
        else:
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))
        return
    # مجموعات
    botun = (context.bot.username or '').lower()
    trigger = (
        (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or
        (botun and f"@{botun}" in (msg.text or msg.caption or '').lower())
    )
    if ct in ['group', 'supergroup'] and trigger:
        sent = await enqueue_mcq(msg, context)
        if not sent:
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return
    sent = await enqueue_mcq(post, context)
    if sent:
        cid = post.chat.id
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('INSERT OR IGNORE INTO channel_stats VALUES(?,0)', (cid,))
            await db.execute(
                'UPDATE channel_stats SET sent=sent+? WHERE chat_id=?',
                (len(send_queues[cid]), cid)
            )
            await db.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')]
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = get_lang(update.effective_user)
    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        txt = get_text('new', lang)
    elif cmd == 'stats':
        async with aiosqlite.connect(DATABASE) as db:
            async with db.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,)) as cur:
                row = await cur.fetchone(); sent = row[0] if row else 0
            async with db.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,)) as cur:
                row = await cur.fetchone(); ch = row[0] if row else 0
        txt = get_text('stats', lang).format(sent=sent, ch=ch)
    else:
        txt = '⚠️ غير مدعوم'
    await update.callback_query.edit_message_text(txt)

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        q = update.inline_query.query
        if not q:
            return
        results = [
            InlineQueryResultArticle(
                id='1', title='تحويل سؤال MCQ',
                input_message_content=InputTextMessageContent(q)
            )
        ]
        await update.inline_query.answer(results)
    except Exception as e:
        logger.error(f"Inline error: {e}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lang = get_lang(update.effective_user)
    await update.message.reply_text(get_text('help', lang))

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    lang = get_lang(update.effective_user)
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,)) as cur:
            row = await cur.fetchone(); sent = row[0] if row else 0
        async with db.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,)) as cur:
            row = await cur.fetchone(); ch = row[0] if row else 0
    await update.message.reply_text(get_text('stats', lang).format(sent=sent, ch=ch))

async def main() -> None:
    await init_db()
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error('❌ TELEGRAM_BOT_TOKEN missing')
        return
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('stats', stats_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.CAPTION), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info('✅ Bot is running...')
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())

