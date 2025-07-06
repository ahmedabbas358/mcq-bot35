# co_mcq_bot.py - النسخة الشاملة بدقة عالية مع دعم القنوات والتنبيهات
import os
import re
import logging
import asyncio
import sqlite3
import time
from collections import defaultdict, deque
from telegram import (
    Update,
    Poll,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    InlineQueryHandler,
    ChannelPostHandler,
    filters,
    ContextTypes
)

# إعداد السجل
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# إعداد قاعدة البيانات SQLite
conn = sqlite3.connect('stats.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS user_stats (
    user_id INTEGER PRIMARY KEY,
    sent INTEGER DEFAULT 0
)''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS channel_stats (
    chat_id INTEGER PRIMARY KEY,
    sent INTEGER DEFAULT 0
)''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS sent_questions (
    chat_id INTEGER,
    hash INTEGER,
    PRIMARY KEY(chat_id, hash)
)''')
conn.commit()

# إعداد الذاكرة لقائمة الانتظار ومنع السبام
send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)

# الخرائط لتحويل الأرقام والحروف العربية
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
    'start': {
        'en': '🤖 Hi! Choose an option:',
        'ar': '🤖 أهلاً! اختر من القائمة:'
    },
    'help': {
        'en': 'Usage:\n- Send MCQ in private.\n- Mention or reply in groups.\n- Formats: Q:/س:',
        'ar': '🆘 كيفية الاستخدام:\n- في الخاص أرسل السؤال.\n- في المجموعات اذكر @البوت أو الرد.\n- الصيغ: Q:/س:'
    },
    'new': {
        'en': '📩 Send your MCQ now!',
        'ar': '📩 أرسل سؤال MCQ الآن!'
    },
    'stats': {
        'en': '📊 You sent {sent} questions.\n✉️ Channel posts: {ch}',
        'ar': '📊 أرسلت {sent} سؤالاً.\n🏷️ منشورات القناة: {ch}'
    },
    'queue_full': {
        'en': '🚫 Queue full, send fewer questions.',
        'ar': '🚫 القائمة ممتلئة، أرسل أقل.'
    },
    'no_q': {
        'en': '❌ No questions detected.',
        'ar': '❌ لم أتعرف على أي سؤال.'
    },
    'error_poll': {
        'en': '⚠️ Failed to send question.',
        'ar': '⚠️ فشل في إرسال السؤال.'
    }
}

def get_text(key, lang):
    return TEXTS[key].get(lang, TEXTS[key]['en'])

# تحليل MCQ مع تفادي التكرارات
def parse_mcq(text, chat_id):
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            h = hash(q)
            cursor.execute('SELECT 1 FROM sent_questions WHERE chat_id=? AND hash=?', (chat_id, h))
            if cursor.fetchone():
                continue
            cursor.execute('INSERT INTO sent_questions(chat_id, hash) VALUES (?, ?)', (chat_id, h))
            conn.commit()
            lines = m.group('opts').strip().splitlines()
            opts = []
            for ln in lines:
                parts = re.split(r'^[A-Za-zء-ي١-٩0-9][).:]\s*', ln.strip(), 1)
                if len(parts) == 2:
                    opts.append(parts[1].strip())
            if not 2 <= len(opts) <= 10:
                continue
            raw = m.group('ans').strip()
            ans = ARABIC_DIGITS.get(raw, raw)
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
            res.append((q, opts, idx))
    return res

# معالجة قائمة الانتظار
async def process_queue(chat_id, context):
    q = send_queues[chat_id]
    while q:
        qst, opts, idx = q.popleft()
        try:
            await context.bot.send_poll(
                chat_id,
                qst,
                opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Failed to send poll: {e}")
            await context.bot.send_message(chat_id, get_text('error_poll', 'ar'))
            break

async def enqueue_mcq(message, context):
    chat_id = message.chat.id
    # حد أقصى للقائمة
    if len(send_queues[chat_id]) > 50:
        lang = (message.from_user.language_code or 'en')[:2]
        await context.bot.send_message(chat_id, get_text('queue_full', lang))
        return False
    text = message.text or message.caption or ''
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    sent = False
    for blk in blocks:
        lst = parse_mcq(blk, chat_id)
        for item in lst:
            send_queues[chat_id].append(item)
            sent = True
    if sent:
        asyncio.create_task(process_queue(chat_id, context))
    return sent

# التعامل مع الرسائل النصية
async def handle_text(update, context):
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return
    uid = msg.from_user.id
    ct = msg.chat.type
    lang = (msg.from_user.language_code or 'en')[:2]
    # منع السبام
    if time.time() - last_sent_time[uid] < 5:
        return
    last_sent_time[uid] = time.time()
    # دردشة خاصة
    if ct == 'private':
        if await enqueue_mcq(msg, context):
            cursor.execute('INSERT OR IGNORE INTO user_stats VALUES (?, 0)', (uid,))
            cursor.execute(
                'UPDATE user_stats SET sent=sent+? WHERE user_id=?',
                (len(send_queues[msg.chat.id]), uid)
            )
            conn.commit()
            try:
                await msg.delete()
            except:
                pass
        else:
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))
        return
    # المجموعات
    botun = context.bot.username.lower() if context.bot.username else ''
    if ct in ['group', 'supergroup'] and (
        (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or
        (botun and f"@{botun}" in (msg.text or msg.caption).lower())
    ):
        if not await enqueue_mcq(msg, context):
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))

# التعامل مع منشورات القناة
async def handle_channel_post(update, context):
    post = update.channel_post
    if post and (post.text or post.caption):
        if await enqueue_mcq(post, context):
            cid = post.chat.id
            cursor.execute('INSERT OR IGNORE INTO channel_stats VALUES (?, 0)', (cid,))
            cursor.execute(
                'UPDATE channel_stats SET sent=sent+? WHERE chat_id=?',
                (len(send_queues[cid]), cid)
            )
            conn.commit()

# أوامر الـ Bot
async def start(update, context):
    lang = (update.effective_user.language_code or 'en')[:2]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')]
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def callback_query_handler(update, context):
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = (update.effective_user.language_code or 'en')[:2]
    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        txt = get_text('new', lang)
    elif cmd == 'stats':
        cursor.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,))
        r = cursor.fetchone()
        sent = r[0] if r else 0
        cursor.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,))
        r = cursor.fetchone()
        ch = r[0] if r else 0
        txt = get_text('stats', lang).format(sent=sent, ch=ch)
    else:
        txt = '⚠️ غير مدعوم'
    await update.callback_query.edit_message_text(txt)

async def inline_query(update, context):
    try:
        q = update.inline_query.query
        if not q:
            return
        results = [
            InlineQueryResultArticle(
                id='1',
                title='تحويل سؤال MCQ',
                input_message_content=InputTextMessageContent(q)
            )
        ]
        await update.inline_query.answer(results)
    except Exception as e:
        logger.error(f"Inline error: {e}")

async def help_command(update, context):
    lang = (update.effective_user.language_code or 'en')[:2]
    await update.message.reply_text(get_text('help', lang))

async def stats_command(update, context):
    uid = update.effective_user.id
    lang = (update.effective_user.language_code or 'en')[:2]
    cursor.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,))
    r = cursor.fetchone()
    sent = r[0] if r else 0
    cursor.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,))
    r = cursor.fetchone()
    ch = r[0] if r else 0
    await update.message.reply_text(get_text('stats', lang).format(sent=sent, ch=ch))

# تشغيل البوت

def main():
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(ChannelPostHandler(handle_channel_post))
    logger.info('✅ Bot is running...')
    app.run_polling()

if __name__ == '__main__':
    main()

