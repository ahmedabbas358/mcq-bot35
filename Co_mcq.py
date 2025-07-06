import os
import re
import logging
import asyncio
import time
import sqlite3
from collections import defaultdict, deque
from telegram import (
    Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

conn = sqlite3.connect('stats.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS sent_questions (chat_id INTEGER, hash INTEGER, PRIMARY KEY(chat_id, hash))''')
cursor.execute('''CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT)''')
conn.commit()

send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)

ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4'}
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3}

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

def get_text(key, lang):
    return TEXTS[key].get(lang, TEXTS[key]['en'])

def parse_mcq(text, chat_id):
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            opts = [o.strip() for o in re.findall(r'[A-Dأ-دA-Za-zء-ي0-9][).:]\s*(.+)', m.group('opts'), re.M)]
            ans = m.group('ans').strip()
            ans = ARABIC_DIGITS.get(ans, ans)
            idx = AR_LETTERS.get(ans, None)
            if idx is None:
                try:
                    idx = int(ans) - 1
                except:
                    continue
            if idx is None or not 0 <= idx < len(opts):
                continue
            res.append((q, opts, idx))
    return res

async def process_queue(chat_id, context):
    q = send_queues[chat_id]
    while q:
        qst, opts, idx = q.popleft()
        try:
            await context.bot.send_poll(chat_id, qst, opts, type=Poll.QUIZ, correct_option_id=idx, is_anonymous=False)
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning(f"Failed to send poll: {e}")

async def enqueue_mcq(message, context):
    chat_id = message.chat.id
    if message.chat.type == 'channel':
        title = message.chat.title or 'Private Channel'
        cursor.execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES(?,?)', (chat_id, title))
        conn.commit()
    if len(send_queues[chat_id]) > 50:
        lang = (getattr(message.from_user, 'language_code', 'en') if message.from_user else 'en')[:2]
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

async def handle_text(update, context):
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return
    uid = msg.from_user.id
    ct = msg.chat.type
    lang = (getattr(msg.from_user, 'language_code', 'en') or 'en')[:2]
    botun = (context.bot.username or '').lower()

    if ct in ['group', 'supergroup']:
        if not ((msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or
                (botun and f"@{botun}" in (msg.text or msg.caption or '').lower())):
            return

    if ct == 'private' and time.time() - last_sent_time[uid] < 5:
        return
    last_sent_time[uid] = time.time()

    if await enqueue_mcq(msg, context):
        cursor.execute('INSERT OR IGNORE INTO user_stats VALUES (?,0)', (uid,))
        cursor.execute('UPDATE user_stats SET sent=sent+? WHERE user_id=?', (len(send_queues[msg.chat.id]), uid))
        conn.commit()
        try:
            await msg.delete()
        except:
            pass
    else:
        await context.bot.send_message(msg.chat.id, get_text('no_q', lang))

async def handle_channel_post(update, context):
    post = update.channel_post
    if not post:
        return

    botun = (context.bot.username or '').lower()
    text = post.text or post.caption or ''
    if f"@{botun}" not in text.lower():
        return

    chat_id = post.chat.id
    title = post.chat.title or 'Private Channel'
    cursor.execute('INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES(?, ?)', (chat_id, title))
    conn.commit()

    if await enqueue_mcq(post, context):
        cursor.execute('INSERT OR IGNORE INTO channel_stats VALUES(?,0)', (chat_id,))
        cursor.execute('UPDATE channel_stats SET sent=sent+? WHERE chat_id=?', (len(send_queues[chat_id]), chat_id))
        conn.commit()
    else:
        try:
            await context.bot.send_message(chat_id, get_text('invalid_format', 'ar'))
        except Exception as e:
            logger.warning(f"Can't send error msg to channel: {e}")

async def callback_query_handler(update, context):
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        txt = get_text('new', lang)
    elif cmd == 'stats':
        r = cursor.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,)).fetchone()
        sent = r[0] if r else 0
        r = cursor.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,)).fetchone()
        ch = r[0] if r else 0
        txt = get_text('stats', lang).format(sent=sent, ch=ch)
    elif cmd == 'channels':
        rows = cursor.execute('SELECT chat_id,title FROM known_channels').fetchall()
        txt = "لا توجد قنوات" if not rows else "\n".join([f"- {t}: {cid}" for cid,t in rows])
    else:
        txt = '⚠️ غير مدعوم'
    await update.callback_query.edit_message_text(txt, parse_mode='Markdown')

async def start(update, context):
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')],
        [InlineKeyboardButton('📺 القنوات', callback_data='channels')]
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def help_command(update, context):
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    await update.message.reply_text(get_text('help', lang))

async def stats_command(update, context):
    uid = update.effective_user.id
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    r = cursor.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,)).fetchone()
    sent = r[0] if r else 0
    r = cursor.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,)).fetchone()
    ch = r[0] if r else 0
    await update.message.reply_text(get_text('stats', lang).format(sent=sent, ch=ch))

async def inline_query(update, context):
    try:
        q = update.inline_query.query
        if not q:
            return
        results = [InlineQueryResultArticle(id='1', title='تحويل سؤال MCQ', input_message_content=InputTextMessageContent(q))]
        await update.inline_query.answer(results)
    except Exception as e:
        logger.error(f"Inline error: {e}")

async def channels_command(update, context):
    rows = cursor.execute('SELECT chat_id,title FROM known_channels').fetchall()
    text = "📡 القنوات المسجلة:\n" + "\n".join([f"- {t}: {cid}" for cid,t in rows]) if rows else "لا توجد قنوات مسجلة بعد."
    await update.message.reply_text(text, parse_mode='Markdown')

def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError("No bot token found.")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('stats', stats_command))
    app.add_handler(CommandHandler('channels', channels_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption()), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    logger.info('✅ Bot is running...')
    app.run_polling()

if __name__ == '__main__':
    main()
