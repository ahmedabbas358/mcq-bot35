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

# إعداد اللوجر
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = 'stats.db'
send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)

# دعم الأرقام العربية والإنجليزية والحروف A-J و أ-ي
ARABIC_DIGITS = {str(i): str(i) for i in range(10)}
ARABIC_DIGITS.update({'٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9'})

EN_LETTERS = {chr(ord('A')+i): i for i in range(10)}
EN_LETTERS.update({chr(ord('a')+i): i for i in range(10)})

AR_LETTERS = {'أ':0,'ب':1,'ج':2,'د':3,'هـ':4,'و':5,'ز':6,'ح':7,'ط':8,'ي':9}

# أنماط regex لدعم حتى 10 خيارات بأنماط متعددة
PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-J1-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j0-9])",
        re.S),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-ي١-٩][).:]\s*.+?(?:\n|$)){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-ي١-٩])",
        re.S),
    re.compile(
        r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|الإجابة|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9])",
        re.S)
]

# نصوص المستخدم باللغتين
TEXTS = {
    'start': {
        'en': '🤖 Hi! Choose an option:',
        'ar': '🤖 أهلاً! اختر من القائمة:'
    },
    'help': {
        'en': '🆘 Usage:\n- Send MCQ in private.\n- To publish in a channel: use 🔄 and choose.\n- In groups: reply or mention @bot.\nExample:\nQ: ...\nA) ...\nB) ...\nAnswer: A',
        'ar': '🆘 كيفية الاستخدام:\n- في الخاص: أرسل السؤال بصيغة Q:/س:.\n- للنشر في قناة: اضغط 🔄 ثم اختر القناة.\n- في المجموعات: رُدّ على البوت أو اذكر @البوت.\nمثال:\nس: ...\nأ) ...\nب) ...\nالإجابة: أ'
    },
    'new': {
        'en': '📩 Send your MCQ now!',
        'ar': '📩 أرسل سؤال MCQ الآن!'
    },
    'stats': {
        'en': '📊 Private: {pr} questions.\n🏷️ Channel: {ch} posts.',
        'ar': '📊 في الخاص: {pr} سؤال.\n🏷️ في القنوات: {ch} منشور.'
    },
    'queue_full': {
        'en': '🚫 Queue full, send fewer questions.',
        'ar': '🚫 القائمة ممتلئة، أرسل أقل.'
    },
    'no_q': {
        'en': '❌ No questions detected.',
        'ar': '❌ لم أتعرف على أي سؤال.'
    },
    'invalid_format': {
        'en': '⚠️ Invalid format.',
        'ar': '⚠️ صيغة غير صحيحة.'
    }
}

def get_text(key, lang):
    return TEXTS[key].get(lang, TEXTS[key]['en'])

# اتصال واحد طوال عمر البوت
_db_conn: aiosqlite.Connection = None

async def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = await aiosqlite.connect(DB_PATH)
    return _db_conn

# إنشاء الجداول
async def init_db(conn):
    await conn.execute('''CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)''')
    await conn.execute('''CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT)''')
    await conn.commit()

# توليد رابط PollBot
async def pollbot_url(q, opts):
    base = q + ':::' + ':::'.join(opts)
    uid = hashlib.md5(base.encode()).hexdigest()
    return f"https://t.me/PollBot?startgroup={uid}"

# تحليل MCQ
def parse_mcq(text):
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            raw = m.group('opts')
            opts = re.findall(r'[A-Za-zأ-ي0-9][).:]\s*(.+)', raw)
            ans = m.group('ans').strip()
            if ans in ARABIC_DIGITS:
                ans = ARABIC_DIGITS[ans]
            idx = EN_LETTERS.get(ans) if ans in EN_LETTERS else AR_LETTERS.get(ans)
            if idx is None:
                try:
                    idx = int(ans) - 1
                except:
                    continue
            if not 0 <= idx < len(opts):
                continue
            res.append((q, opts, idx))
    return res

# تنظيف دوري كل 24 ساعة
async def schedule_cleanup():
    while True:
        await asyncio.sleep(86400)
        conn = await get_db()
        await conn.execute("DELETE FROM known_channels WHERE chat_id NOT IN (SELECT chat_id FROM channel_stats WHERE sent>0)")
        await conn.execute("DELETE FROM user_stats WHERE sent=0")
        await conn.commit()

# معالجة الطابور
async def process_queue(chat_id, context, msg_id=None, is_private=False):
    conn = await get_db()
    while send_queues[chat_id]:
        q, opts, idx = send_queues[chat_id].popleft()
        try:
            poll = await context.bot.send_poll(
                chat_id, q, opts,
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False
            )
            await asyncio.sleep(0.5)
            # حذف رسالة المستخدم
            if msg_id:
                try:
                    await context.bot.delete_message(chat_id, msg_id)
                except:
                    pass
            # نشر رابط PollBot
            url = await pollbot_url(q, opts)
            await context.bot.send_message(chat_id, f"📢 شارك التصويت: {url}")
            # تحديث الإحصائيات
            if is_private:
                user_id = context.user_data.get('user_id')
                await conn.execute('INSERT OR IGNORE INTO user_stats(user_id,sent) VALUES (?,0)', (user_id,))
                await conn.execute('UPDATE user_stats SET sent=sent+1 WHERE user_id=?', (user_id,))
            else:
                await conn.execute('INSERT OR IGNORE INTO channel_stats(chat_id,sent) VALUES (?,0)', (chat_id,))
                await conn.execute('UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?', (chat_id,))
            await conn.commit()
        except Exception as e:
            logger.warning(f"send_poll failed: {e}")

# إضافة سؤال للطابور
async def enqueue_mcq(msg, context, override=None, is_private=False):
    cid = override or msg.chat.id
    lang = (getattr(msg.from_user, 'language_code', 'en') or 'en')[:2]
    if len(send_queues[cid]) > 50:
        await context.bot.send_message(cid, get_text('queue_full', lang))
        return False
    text = msg.text or msg.caption or ''
    blocks = [b for b in re.split(r"\n{2,}", text) if b.strip()]
    found = False
    for blk in blocks:
        for item in parse_mcq(blk):
            send_queues[cid].append(item)
            found = True
    if found:
        context.user_data['user_id'] = msg.from_user.id
        asyncio.create_task(process_queue(cid, context, msg.message_id, is_private))
    return found

# حدث استقبال نص
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not (msg.text or msg.caption):
        return
    uid = msg.from_user.id
    # منع التكرار السريع
    if time.time() - last_sent_time[uid] < 5:
        return
    last_sent_time[uid] = time.time()
    ct = msg.chat.type
    botun = (context.bot.username or '').lower()
    # في الخاص
    if ct == 'private':
        target = context.user_data.get('target_channel')
        if await enqueue_mcq(msg, context, override=target, is_private=True):
            return await context.bot.send_message(msg.chat.id, get_text('no_q', (getattr(msg.from_user, 'language_code', 'en') or 'en')[:2]))
        return
    # في المجموعات
    content = (msg.text or msg.caption or '').lower()
    if ct in ['group', 'supergroup'] and (
        (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or
        f"@{botun}" in content
    ):
        if await enqueue_mcq(msg, context, is_private=False):
            try:
                await context.bot.delete_message(msg.chat.id, msg.message_id)
            except:
                pass
    else:
        await context.bot.send_message(msg.chat.id, get_text('invalid_format', (getattr(msg.from_user,'language_code','en') or 'en')[:2]))

# حدث لاستقبال منشور في القناة
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    text = (post.text or post.caption or '')
    botun = (context.bot.username or '').lower()
    if f"@{botun}" not in text.lower():
        return
    # حفظ القناة
    conn = await get_db()
    await conn.execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES(?,?)', (post.chat.id, post.chat.title or ''))
    await conn.commit()
    # enqueue
    await enqueue_mcq(post, context, is_private=False)

# أوامر البداية وInline
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = (getattr(update.effective_user,'language_code','en') or 'en')[:2]
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
    lang = (getattr(update.effective_user,'language_code','en') or 'en')[:2]
    conn = await get_db()
    txt = '⚠️ غير مدعوم'

    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        txt = get_text('new', lang)
    elif cmd == 'stats':
        async with conn.execute('SELECT sent FROM user_stats WHERE user_id=?', (uid,)) as cur:
            r = await cur.fetchone()
            pr = r[0] if r else 0
        chat_id = update.effective_chat.id
        async with conn.execute('SELECT sent FROM channel_stats WHERE chat_id=?', (chat_id,)) as cur:
            r2 = await cur.fetchone()
            ch = r2[0] if r2 else 0
        txt = get_text('stats', lang).format(pr=pr, ch=ch)
    elif cmd == 'channels':
        rows = []
        async with conn.execute('SELECT chat_id,title FROM known_channels') as cur:
            rows = await cur.fetchall()
        txt = 'لا توجد قنوات' if not rows else '\n'.join(f"- {t}: {cid}" for cid,t in rows)
    elif cmd == 'publish_channel':
        rows = []
        async with conn.execute('SELECT chat_id,title FROM known_channels') as cur:
            rows = await cur.fetchall()
        if not rows:
            txt = '❌ لا قنوات.'
            await update.callback_query.edit_message_text(txt)
            return
        kb = [[InlineKeyboardButton(t, callback_data=f'choose_{cid}')] for cid,t in rows]
        await update.callback_query.edit_message_text('اختر قناة:', reply_markup=InlineKeyboardMarkup(kb))
        return
    elif cmd.startswith('choose_'):
        cid = int(cmd.split('_')[1])
        async with conn.execute('SELECT title FROM known_channels WHERE chat_id=?', (cid,)) as cur:
            row = await cur.fetchone()
        if not row:
            txt = '❌ غير موجود'
        else:
            context.user_data['target_channel'] = cid
            txt = f"✅ قناة محددة: {row[0]}. أرسل السؤال في الخاص."
    await update.callback_query.edit_message_text(txt, parse_mode='Markdown')

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.inline_query.query
    if not q:
        return
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

    logger.info('✅ Bot running')
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
