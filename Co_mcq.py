import os
import re
import logging
import asyncio
import time
import hashlib
from collections import defaultdict, deque

import aiosqlite
from telegram import (
    Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup,
    InlineQueryResultArticle, InputTextMessageContent
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes
)
from telegram.constants import ChatType

# إعداد اللوجر
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = 'stats.db'
send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)
_db_conn: aiosqlite.Connection = None

# دعم الأرقام العربية والإنجليزية والحروف A-J و أ-ي
ARABIC_DIGITS = {'٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9'}
ARABIC_DIGITS.update({str(i): str(i) for i in range(10)})
EN_LETTERS = {chr(ord('A')+i): i for i in range(10)}
EN_LETTERS.update({chr(ord('a')+i): i for i in range(10)})
AR_LETTERS = {'أ':0,'ب':1,'ج':2,'د':3,'هـ':4,'و':5,'ز':6,'ح':7,'ط':8,'ي':9}

# أنماط regex
PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-J1-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j0-9])", re.S),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-ي١-٩][).:]\s*.+?(?:\n|$)){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-ي١-٩])", re.S),
    re.compile(
        r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|الإجابة|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9])", re.S)
]

# رسائل
TEXTS = {
    'start': {'en': '🤖 Hi! Choose an option:', 'ar': '🤖 أهلاً! اختر من القائمة:'},
    'help': {
        'en': '🆘 Usage:\n- Send MCQ in private.\n- To publish in a channel: use 🔄 and choose.\n- In groups: reply or mention @bot.\nExample:\nQ: ...\nA) ...\nB) ...\nAnswer: A',
        'ar': '🆘 كيفية الاستخدام:\n- في الخاص: أرسل السؤال بصيغة Q:/س:.\n- للنشر في قناة: اضغط 🔄 ثم اختر القناة.\n- في المجموعات: رُدّ على البوت أو اذكر @البوت.\nمثال:\nس: ...\nأ) ...\nب) ...\nالإجابة: أ'
    },
    'new': {'en': '📩 Send your MCQ now!', 'ar': '📩 أرسل سؤال MCQ الآن!'},
    'stats': {'en': '📊 Private: {pr} questions.\n🏷️ Channel: {ch} posts.', 'ar': '📊 في الخاص: {pr} سؤال.\n🏷️ في القنوات: {ch} منشور.'},
    'queue_full': {'en': '🚫 Queue full, send fewer questions.', 'ar': '🚫 القائمة ممتلئة، أرسل أقل.'},
    'no_q': {'en': '❌ No questions detected.', 'ar': '❌ لم أتعرف على أي سؤال.'},
    'invalid_format': {'en': '⚠️ Invalid format.', 'ar': '⚠️ صيغة غير صحيحة.'},
    'pollbot_link': {'en': '📢 Share vote: {link}', 'ar': '📢 شارك التصويت: {link}'}
}

def get_text(key, lang, **kwargs):
    return TEXTS[key].get(lang, TEXTS[key]['en']).format(**kwargs)

async def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH)
    return _db_conn

async def init_db(conn):
    await conn.execute('CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)')
    await conn.execute('CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)')
    await conn.execute('CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT)')
    await conn.commit()

async def schedule_cleanup():
    while True:
        try:
            await asyncio.sleep(86400)
            conn = await get_db()
            await conn.execute("DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent>0)")
            await conn.execute("DELETE FROM user_stats WHERE sent=0")
            await conn.commit()
        except Exception as e:
            logger.warning(f"Cleanup error: {e}")

def parse_mcq(text):
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            raw = m.group('opts')
            opts = re.findall(r'[A-Za-zأ-ي0-9][).:]\s*(.+)', raw)
            ans = m.group('ans').strip()
            if ans in ARABIC_DIGITS: ans = ARABIC_DIGITS[ans]
            idx = EN_LETTERS.get(ans) or AR_LETTERS.get(ans)
            if idx is None:
                try: idx = int(ans) - 1
                except: continue
            if not (0 <= idx < len(opts)): continue
            res.append((q, opts, idx))
    return res

async def process_queue(chat_id, context, user_id=None, msg_id=None, is_private=False):
    conn = await get_db()
    deleted = False
    while send_queues[chat_id]:
        q, opts, idx = send_queues[chat_id].popleft()
        try:
            await context.bot.send_poll(chat_id, q, opts, type=Poll.QUIZ, correct_option_id=idx, is_anonymous=False)
            await asyncio.sleep(0.5)
            if msg_id and not deleted:
                try:
                    await context.bot.delete_message(chat_id, msg_id)
                    deleted = True
                except: pass
            link = f"https://t.me/PollBot?startgroup={hashlib.md5((q+':::'.join(opts)).encode()).hexdigest()}"
            lang = context.user_data.get('lang', 'en')
            await context.bot.send_message(chat_id, get_text('pollbot_link', lang, link=link))
            if is_private:
                await conn.execute('INSERT OR IGNORE INTO user_stats(user_id,sent) VALUES (?,0)', (user_id,))
                await conn.execute('UPDATE user_stats SET sent=sent+1 WHERE user_id=?', (user_id,))
            else:
                await conn.execute('INSERT OR IGNORE INTO channel_stats(chat_id,sent) VALUES (?,0)', (chat_id,))
                await conn.execute('UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?', (chat_id,))
            await conn.commit()
        except Exception as e:
            logger.warning(f"Send poll failed: {e}")

async def enqueue_mcq(msg, context, override=None, is_private=False):
    cid = override or context.user_data.get("target_channel") or msg.chat.id
    lang = (msg.from_user.language_code or 'en')[:2]
    context.user_data['lang'] = lang
    if len(send_queues[cid]) > 50:
        await context.bot.send_message(cid, get_text('queue_full', lang))
        return False
    text = msg.text or msg.caption or ''
    blocks = [b for b in re.split(r"\n{2,}", text) if b.strip()]
    found = False
    for blk in blocks:
        for q, o, i in parse_mcq(blk):
            send_queues[cid].append((q, o, i))
            found = True
    if found:
        asyncio.create_task(process_queue(cid, context, user_id=msg.from_user.id, msg_id=msg.message_id, is_private=is_private))
    return found

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not (msg.text or msg.caption): return
    uid = msg.from_user.id
    if time.time() - last_sent_time[uid] < 3: return
    last_sent_time[uid] = time.time()

    chat_type = msg.chat.type
    if chat_type == ChatType.PRIVATE:
        found = await enqueue_mcq(msg, context, is_private=True)
        if not found:
            lang = (msg.from_user.language_code or 'en')[:2]
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))
        return

    content = (msg.text or msg.caption or '').lower()
    if chat_type in ['group', 'supergroup'] and (
        (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or
        f"@{context.bot.username.lower()}" in content
    ):
        await enqueue_mcq(msg, context, is_private=False)
        try: await context.bot.delete_message(msg.chat.id, msg.message_id)
        except: pass
    else:
        lang = (msg.from_user.language_code or 'en')[:2]
        await context.bot.send_message(msg.chat.id, get_text('invalid_format', lang))

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post: return
    if f"@{context.bot.username.lower()}" not in (post.text or post.caption or '').lower():
        return
    conn = await get_db()
    await conn.execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES (?,?)', (post.chat.id, post.chat.title or ''))
    await conn.commit()
    await enqueue_mcq(post, context, is_private=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    context.user_data['lang'] = lang
    kb = [
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('🔄 نشر في قناة', callback_data='publish_channel')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')],
        [InlineKeyboardButton('📺 القنوات', callback_data='channels')],
    ]
    await update.message.reply_text(get_text('start', lang), reply_markup=InlineKeyboardMarkup(kb))

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = context.user_data.get('lang', 'en')
    conn = await get_db()
    txt = '⚠️ غير مدعوم'

    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        txt = get_text('new', lang)
    elif cmd == 'stats':
        r = await (await conn.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,))).fetchone()
        s = await (await conn.execute('SELECT SUM(sent) FROM channel_stats')).fetchone()
        txt = get_text('stats', lang, pr=r[0] if r else 0, ch=s[0] if s and s[0] else 0)
    elif cmd == 'channels':
        rows = await (await conn.execute('SELECT chat_id,title FROM known_channels')).fetchall()
        txt = 'لا توجد قنوات' if not rows else '\n'.join(f"- {t}: `{cid}`" for cid, t in rows)
    elif cmd == 'publish_channel':
        rows = await (await conn.execute('SELECT chat_id,title FROM known_channels')).fetchall()
        if not rows:
            await update.callback_query.edit_message_text('❌ لا توجد قنوات.')
            return
        kb = [[InlineKeyboardButton(t, callback_data=f'choose_{cid}')] for cid, t in rows]
        await update.callback_query.edit_message_text('اختر قناة:', reply_markup=InlineKeyboardMarkup(kb))
        return
    elif cmd.startswith('choose_'):
        cid = int(cmd.split('_')[1])
        row = await (await conn.execute('SELECT title FROM known_channels WHERE chat_id=?', (cid,))).fetchone()
        if row:
            context.user_data['target_channel'] = cid
            txt = f"✅ قناة محددة: {row[0]}. أرسل السؤال في الخاص."
        else:
            txt = '❌ غير موجود'
    await update.callback_query.edit_message_text(txt, parse_mode='Markdown')

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query
    if not q: return
    result = InlineQueryResultArticle(
        id='1',
        title='MCQ تحويل',
        input_message_content=InputTextMessageContent(q)
    )
    await update.inline_query.answer([result])

async def main():
    conn = await get_db()
    await init_db(conn)
    asyncio.create_task(schedule_cleanup())

    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError('❌ Bot token not found.')

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler(['start', 'help'], start))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption()), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("✅ Bot is running...")
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
