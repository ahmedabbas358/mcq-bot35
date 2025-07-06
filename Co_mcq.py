import os
import re
import logging
import asyncio
import time

import aiosqlite
from collections import defaultdict, deque

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

# ===== إعداد السجل =====
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== متغيرات عامة =====
DB_PATH = 'stats.db'
MAX_QUEUE = 50
RATE_LIMIT_SECONDS = 5

send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)
db_lock = asyncio.Lock()

# دعم الأرقام والحروف العربية والإنجليزية
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4'}
AR_LETTERS = {
    'أ': 0, 'ب': 1, 'ج': 2, 'د': 3,
    'A': 0, 'B': 1, 'C': 2, 'D': 3,
    '1': 0, '2': 1, '3': 2, '4': 3
}

PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-D][).:]\s*.+?\s*){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Da-d1-4١-٤])",
        re.S | re.IGNORECASE
    ),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-د][).:]\s*.+?\s*){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-د1-4١-٤])",
        re.S
    ),
    re.compile(
        r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9]+[).:]\s*.+?\n){2,10})"
        r"(?:Answer|الإجابة|Ans|Correct Answer)[:：]?"
        r"\s*(?P<ans>[A-Za-zء-ي0-9١-٤])",
        re.S | re.IGNORECASE
    )
]

TEXTS = {
    'start': {'en': '🤖 Hi! Choose an option:', 'ar': '🤖 أهلاً! اختر من القائمة:'},
    'help': {'en': 'Usage:\n/addchannel - Register a channel\n/createpoll - Create a poll',
             'ar': '🆘 /addchannel - إضافة قناة\n/createpoll - إنشاء استطلاع'},
    'new': {'en': '📩 Send your MCQ now!', 'ar': '📩 أرسل سؤال MCQ الآن!'},
    'stats': {'en': '📊 You sent {sent} questions.\n✉️ Channel posts: {ch}',
              'ar': '📊 أرسلت {sent} سؤالاً.\n🏷️ منشورات القناة: {ch}'},
    'queue_full': {'en': '🚫 Queue full, try later.', 'ar': '🚫 قائمة الانتظار ممتلئة، حاول لاحقاً.'},
    'no_q': {'en': '❌ No questions detected.', 'ar': '❌ لم أتعرف على أي سؤال.'},
    'invalid_format': {'en': '⚠️ Invalid MCQ format.', 'ar': '⚠️ صيغة السؤال غير صحيحة.'},
    'added': {'en': '✅ Channel added successfully.', 'ar': '✅ تمت إضافة القناة بنجاح.'},
    'add_fail': {'en': '❌ Failed to add channel. Check bot permissions.',
                 'ar': '❌ فشل إضافة القناة. تحقق من صلاحيات البوت.'},
    'no_channels': {'en': '❌ You have no registered channels. Use /addchannel.',
                    'ar': '❌ لم تضف أي قناة. استخدم /addchannel.'}
}

def get_text(key: str, lang: str = 'en') -> str:
    return TEXTS.get(key, {}).get(lang, TEXTS[key]['en'])

# ===== تجهيز قاعدة البيانات =====
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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
            CREATE TABLE IF NOT EXISTS known_channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_channels (
                user_id INTEGER,
                chat_id INTEGER,
                PRIMARY KEY(user_id, chat_id)
            )
        ''')
        await db.commit()

# ===== تحليل أسئلة MCQ =====
def parse_mcq(text: str) -> list[tuple[str, list[str], int]]:
    results = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            opts = [o.strip() for o in re.findall(r'[A-Dأ-دA-Za-zء-ي0-9][).:]\s*(.+)', m.group('opts'), re.M)]
            ans_raw = m.group('ans').strip()
            ans = ARABIC_DIGITS.get(ans_raw, ans_raw.upper())
            idx = AR_LETTERS.get(ans, None)
            if idx is None:
                try:
                    idx = int(ans) - 1
                except:
                    continue
            if 0 <= idx < len(opts):
                results.append((q, opts, idx))
    return results

# ===== إرسال من قائمة الانتظار =====
async def process_queue(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    queue = send_queues[chat_id]
    while queue:
        q, opts, idx = queue.popleft()
        try:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=q,
                options=opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False
            )
            await asyncio.sleep(1)  # تخفيف الحمل
        except Exception as e:
            logger.error(f"Error sending poll to {chat_id}: {e}")

# ===== إضافة سؤال إلى الطابور =====
async def enqueue_mcq(message, context):
    chat_id = message.chat.id
    if message.chat.type == 'channel':
        async with db_lock, aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                'INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES(?, ?)',
                (chat_id, message.chat.title or '')
            )
            await db.commit()

    if len(send_queues[chat_id]) >= MAX_QUEUE:
        lang = (message.from_user.language_code or 'en')[:2]
        await context.bot.send_message(chat_id, get_text('queue_full', lang))
        return False

    text = message.text or message.caption or ''
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    sent_any = False

    for blk in blocks:
        for q, opts, idx in parse_mcq(blk):
            send_queues[chat_id].append((q, opts, idx))
            sent_any = True

    if sent_any:
        asyncio.create_task(process_queue(chat_id, context))
    return sent_any

# ===== /addchannel =====
async def addchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['awaiting_channel'] = True
    await update.message.reply_text(
        "📢 أرسل رسالة معادة من القناة أو @username الخاص بها لإضافتها."
    )

# ===== معالجة إضافة القناة =====
async def handle_addchannel_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get('awaiting_channel'):
        return False
    context.user_data['awaiting_channel'] = False

    user_id = update.effective_user.id
    msg = update.message
    chat_id = None

    if msg.forward_from_chat:
        chat_id = msg.forward_from_chat.id
    elif msg.text and msg.text.startswith('@'):
        try:
            chat = await context.bot.get_chat(msg.text)
            chat_id = chat.id
        except:
            await msg.reply_text(get_text('add_fail', (msg.from_user.language_code or 'en')[:2]))
            return True

    if not chat_id:
        await msg.reply_text(get_text('add_fail', (msg.from_user.language_code or 'en')[:2]))
        return True

    try:
        member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if member.status not in ('administrator', 'creator'):
            raise Exception("Not admin")
    except:
        await msg.reply_text(get_text('add_fail', (msg.from_user.language_code or 'en')[:2]))
        return True

    async with db_lock, aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            'INSERT OR IGNORE INTO user_channels(user_id, chat_id) VALUES(?, ?)',
            (user_id, chat_id)
        )
        await db.commit()

    await msg.reply_text(get_text('added', (msg.from_user.language_code or 'en')[:2]))
    return True

# ===== /createpoll =====
async def createpoll_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = (update.effective_user.language_code or 'en')[:2]

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            'SELECT chat_id FROM user_channels WHERE user_id=?', (user_id,)
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text(get_text('no_channels', lang))
        return

    buttons = []
    for (cid,) in rows:
        chat = await context.bot.get_chat(cid)
        buttons.append([InlineKeyboardButton(chat.title or str(cid), callback_data=f"poll_channel_{cid}")])
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("اختر القناة لإنشاء الاستطلاع:", reply_markup=keyboard)

# ===== معالجات الأزرار =====
async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data.startswith('poll_channel_'):
        cid = int(data.split('_')[-1])
        url = f"https://t.me/PollBot?startgroup={cid}"
        await update.callback_query.edit_message_text(f"اضغط الرابط لإنشاء الاستطلاع في القناة:\n\n{url}")
        return True
    return False

# ===== التعامل مع منشورات القناة عند الذكر =====
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    botun = context.bot.username.lower()
    text = post.text or post.caption or ''
    if f"@{botun}" not in text.lower():
        return
    if not await enqueue_mcq(post, context):
        await context.bot.send_message(post.chat.id, get_text('invalid_format', 'ar'))
    else:
        async with db_lock, aiosqlite.connect(DB_PATH) as db:
            await db.execute('INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES(?, 0)', (post.chat.id,))
            await db.execute('UPDATE channel_stats SET sent = sent + 1 WHERE chat_id=?', (post.chat.id,))
            await db.commit()

# ===== التعامل مع النص العام =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return
    uid = msg.from_user.id
    ct = msg.chat.type
    lang = (msg.from_user.language_code or 'en')[:2]
    now = time.time()

    if ct == 'private':
        if now - last_sent_time[uid] < RATE_LIMIT_SECONDS:
            return
        last_sent_time[uid] = now
        if await enqueue_mcq(msg, context):
            async with db_lock, aioqsqlite.connect(DB_PATH) as db:
                await db.execute('INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES(?, 0)', (uid,))
                await db.execute('UPDATE user_stats SET sent = sent + 1 WHERE user_id=?', (uid,))
                await db.commit()
            try:
                await msg.delete()
            except:
                pass
        else:
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))

    elif ct in ('group', 'supergroup'):
        botun = context.bot.username.lower()
        text = msg.text or ''
        if (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or (f"@{botun}" in text.lower()):
            if not await enqueue_mcq(msg, context):
                await context.bot.send_message(msg.chat.id, get_text('invalid_format', lang))

# ===== أوامر مساعدة =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = (update.effective_user.language_code or 'en')[:2]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')],
        [InlineKeyboardButton('📺 قنواتي', callback_data='channels')],
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = (update.effective_user.language_code or 'en')[:2]
    await update.message.reply_text(get_text('help', lang))

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = (update.effective_user.language_code or 'en')[:2]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,)) as c1:
            row1 = await c1.fetchone()
        async with db.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,)) as c2:
            row2 = await c2.fetchone()
    sent = row1[0] if row1 else 0
    ch = row2[0] if row2 else 0
    await update.message.reply_text(get_text('stats', lang).format(sent=sent, ch=ch))

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = (update.effective_user.language_code or 'en')[:2]
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT chat_id FROM user_channels WHERE user_id=?', (uid,)) as c:
            rows = await c.fetchall()
    if not rows:
        await update.message.reply_text(get_text('no_channels', lang))
        return
    text = "📡 قنواتك المسجلة:\n"
    for (cid,) in rows:
        chat = await context.bot.get_chat(cid)
        text += f"- {chat.title or cid}: `{cid}`\n"
    await update.message.reply_text(text, parse_mode='Markdown')

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query.strip()
    if not q:
        return
    result = InlineQueryResultArticle(
        id='1', title='تحويل سؤال MCQ',
        input_message_content=InputTextMessageContent(q)
    )
    await update.inline_query.answer([result])

# ===== نقطة الدخول =====
async def main():
    await init_db()
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("No bot token provided.")
        return

    app = Application.builder().token(token).build()

    # أوامر أساسية
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('stats', stats_command))
    app.add_handler(CommandHandler('channels', channels_command))

    # إدارة القنوات
    app.add_handler(CommandHandler('addchannel', addchannel_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_addchannel_msg))
    app.add_handler(CommandHandler('createpoll', createpoll_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    # التعامل مع MCQ
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption()), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(InlineQueryHandler(inline_query))

    logger.info("✅ Bot is running...")
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
