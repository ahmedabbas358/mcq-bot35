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
    InlineQueryHandler, filters, ContextTypes, Webhook
)

# ===== إعداد سجل الأخطاء =====
logging.basicConfig(
    filename='bot_errors.log',
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

# ===== متغيرات لمنع سبام وإدارة الطوابير =====
send_queues       = defaultdict(asyncio.Queue)
last_sent_user    = defaultdict(float)
last_sent_chat    = defaultdict(float)
send_locks        = defaultdict(asyncio.Lock)
queue_tasks       = {}
user_time_lock    = asyncio.Lock()
chat_time_lock    = asyncio.Lock()

# ===== دعم الحروف العربية والأرقام =====
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4'}
AR_LETTERS    = {'أ':0, 'ب':1, 'ج':2, 'د':3}

# ===== تعابير لمطابقة MCQ =====
PATTERNS = [
    re.compile(r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-D][).:]\s*.+?\s*){2,10})(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Da-d1-4١-٤])", re.S|re.IGNORECASE),
    re.compile(r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-د][).:]\s*.+?\s*){2,10})الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-د1-4١-٤])", re.S),
    re.compile(r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9]+[).:]\s*.+?\n){2,10})(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9١-٤])", re.S|re.IGNORECASE)
]

# ===== نصوص متعدد اللغات =====
TEXTS = {
    'start': {'en':'🤖 Hi! Choose an option:','ar':'🤖 أهلاً! اختر من القائمة:'},
    'help' : {'en':'Usage:\n- Send MCQ in private.\n- Mention or reply in groups.\n- Formats: Q:/س:','ar':'🆘 كيفية الاستخدام:\n- في الخاص أرسل السؤال.\n- في المجموعات اذكر @البوت أو الرد.\n- الصيغ: Q:/س:'},
    'new'  : {'en':'📩 Send your MCQ now!','ar':'📩 أرسل سؤال MCQ الآن!'},
    'stats': {'en':'📊 You sent {sent} questions.\n✉️ Channel posts: {ch}','ar':'📊 أرسلت {sent} سؤالاً.\n🏷️ منشورات القناة: {ch}'},
    'queue_full':{'en':'🚫 Queue full, send fewer questions.','ar':'🚫 القائمة ممتلئة، أرسل أقل.'},
    'no_q':{'en':'❌ No questions detected.','ar':'❌ لم أتعرف على أي سؤال.'},
    'error_poll':{'en':'⚠️ Failed to send question.','ar':'⚠️ فشل في إرسال السؤال.'},
    'invalid_format':{'en':'⚠️ Please send a properly formatted MCQ.','ar':'⚠️ الرجاء إرسال السؤال بصيغة صحيحة.'}
}

def get_text(key, lang):
    return TEXTS[key].get(lang, TEXTS[key]['en'])

def get_lang(obj):
    lang = getattr(obj, 'language_code', None)
    return lang[:2].lower() if lang and len(lang)>=2 else 'en'

def hash_question(txt):
    n = re.sub(r'\s+',' ', txt.lower().strip())
    return hashlib.sha256(n.encode()).hexdigest()

async def init_db():
    async with aiosqlite.connect('stats.db') as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS user_stats    (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS sent_questions(chat_id INTEGER, hash TEXT, PRIMARY KEY(chat_id,hash));
            CREATE TABLE IF NOT EXISTS known_channels(chat_id INTEGER PRIMARY KEY, title TEXT);
        """)
        await db.commit()

def split_options(raw):
    lines = raw.strip().splitlines()
    pattern = re.compile(r'^[A-Za-zأ-د][).:]\s*(.+)$')
    return [m.group(1).strip() for ln in lines if (m:=pattern.match(ln.strip()))]

async def parse_mcq(text, chat_id):
    results, hashes = [], []
    async with aiosqlite.connect('stats.db') as db:
        for patt in PATTERNS:
            for m in patt.finditer(text):
                q = m.group('q').strip()
                h = hash_question(q)
                if await db.execute_fetchone('SELECT 1 FROM sent_questions WHERE chat_id=? AND hash=?',(chat_id,h)):
                    continue
                opts = split_options(m.group('opts'))
                if not (2<=len(opts)<=10): continue
                raw = m.group('ans').strip()
                ans = ARABIC_DIGITS.get(raw,raw)
                idx = None
                try:
                    idx = int(ans)-1 if ans.isdigit() else ord(ans.lower())-97 if ans.lower() in 'abcd' else AR_LETTERS.get(raw)
                except: pass
                if idx is None or not 0<=idx<len(opts): continue
                results.append((q,opts,idx)); hashes.append((chat_id,h))
        if hashes:
            await db.executemany('INSERT INTO sent_questions(chat_id,hash) VALUES (?,?)',hashes)
            await db.commit()
    return results

async def process_queue(chat_id, context):
    async with send_locks[chat_id]:
        q = send_queues[chat_id]
        while not q.empty():
            qst,opts,idx = await q.get()
            try:
                await context.bot.send_poll(chat_id,qst,opts,type=Poll.QUIZ,correct_option_id=idx,is_anonymous=False)
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Poll to {chat_id} failed: {e}")
                await context.bot.send_message(chat_id,get_text('error_poll','ar'))
                break
    queue_tasks.pop(chat_id,None)

async def enqueue_mcq(msg,context):
    cid=msg.chat.id
    if msg.chat.type=='channel':
        title=msg.chat.title or 'Private Channel'
        async with aiosqlite.connect('stats.db') as db:
            await db.execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES(?,?)',(cid,title))
            await db.commit()
    if send_queues[cid].qsize()>50:
        await context.bot.send_message(cid,get_text('queue_full',get_lang(msg.from_user)))
        return False
    txt=msg.text or msg.caption or ''
    if not txt.strip(): return False
    blocks=[b.strip() for b in re.split(r'\n{2,}',txt) if b.strip()]
    sent=False
    for blk in blocks:
        for item in await parse_mcq(blk,cid):
            await send_queues[cid].put(item); sent=True
    if sent and cid not in queue_tasks:
        queue_tasks[cid]=asyncio.create_task(process_queue(cid,context))
    return sent

async def can_send(id_,is_user):
    now=time.time()
    if is_user:
        async with user_time_lock:
            last=last_sent_user.get(id_,0)
            if now-last<5: return False
            last_sent_user[id_]=now
    else:
        async with chat_time_lock:
            last=last_sent_chat.get(id_,0)
            if now-last<3: return False
            last_sent_chat[id_]=now
    return True

async def handle_text(update,context):
    msg=update.message
    if not msg or not (msg.text or msg.caption) or not msg.from_user: return
    uid,ct=getattr(msg.from_user,'id'),msg.chat.type
    if ct=='private':
        if not await can_send(uid,True): return
        ok=await enqueue_mcq(msg,context)
        if ok:
            async with aiosqlite.connect('stats.db') as db:
                await db.execute('INSERT OR IGNORE INTO user_stats VALUES (?,0)',(uid,))
                await db.execute('UPDATE user_stats SET sent=sent+1 WHERE user_id=?',(uid,))
                await db.commit()
            await msg.delete()
        else:
            await context.bot.send_message(msg.chat.id,get_text('no_q',get_lang(msg.from_user)))
    else:
        if not await can_send(msg.chat.id,False): return
        botun=(context.bot.username or '').lower()
        txt=(msg.text or msg.caption or '').lower()
        if f"@{botun}" in txt or (msg.reply_to_message and msg.reply_to_message.from_user.id==context.bot.id):
            if msg.chat.type=='channel':
                async with aiosqlite.connect('stats.db') as db:
                    await db.execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES(?,?)',(msg.chat.id,msg.chat.title or ''))
                    await db.commit()
            if await enqueue_mcq(msg,context):
                async with aiosqlite.connect('stats.db') as db:
                    await db.execute('INSERT OR IGNORE INTO channel_stats VALUES (?,0)',(msg.chat.id,))
                    await db.execute('UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?',(msg.chat.id,))
                    await db.commit()
            else:
                await context.bot.send_message(msg.chat.id,get_text('invalid_format',get_lang(msg.from_user)))

async def handle_channel_post(update,context):
    post=update.channel_post
    if not post: return
    cid=post.chat.id
    async with aiosqlite.connect('stats.db') as db:
        await db.execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES(?,?)',(cid,post.chat.title or ''))
        await db.commit()
    await enqueue_mcq(post,context)

async def channel_id_cmd(update,context):
    chat=update.effective_chat
    if chat.type=='channel':
        await update.message.reply_text(f"📡 Channel ID: `{chat.id}`",parse_mode='Markdown')
    else:
        await update.message.reply_text("⚠️ داخل قناة فقط.")

async def start_cmd(update,context):
    lang=get_lang(update.effective_user)
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد',callback_data='new')],
        [InlineKeyboardButton('📊 إحصائياتي',callback_data='stats')],
        [InlineKeyboardButton('📘 مساعدة',callback_data='help')],
        [InlineKeyboardButton('📺 القنوات',callback_data='channels')],
    ])
    await update.message.reply_text(get_text('start',lang),reply_markup=kb)

async def callback_handler(update,context):
    cmd=update.callback_query.data
    uid=update.effective_user.id
    cid=update.effective_chat.id
    lang=get_lang(update.effective_user)
    if cmd=='help': txt=get_text('help',lang)
    elif cmd=='new': txt=get_text('new',lang)
    elif cmd=='stats':
        async with aiosqlite.connect('stats.db') as db:
            r=await db.execute_fetchone('SELECT sent FROM user_stats WHERE user_id=?',(uid,))
            sent=r[0] if r else 0
            r=await db.execute_fetchone('SELECT sent FROM channel_stats WHERE chat_id=?',(cid,))
            ch=r[0] if r else 0
        txt=get_text('stats',lang).format(sent=sent,ch=ch)
    elif cmd=='channels':
        async with aiosqlite.connect('stats.db') as db:
            rows=await db.execute_fetchall('SELECT chat_id,title FROM known_channels')
        txt="لا توجد قنوات" if not rows else "\n".join(f"- {t}: `{i}`" for i,t in rows)
    else: txt="⚠️ أمر غير معروف."
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(txt,parse_mode='Markdown')

async def inline_handler(update,context):
    q=update.inline_query.query.strip()
    if not q: return
    await update.inline_query.answer([
        InlineKeyboardResultArticle(
            id='1', title='تحويل MCQ',
            input_message_content=InputTextMessageContent(q)
        )
    ])

async def main_async():
    await init_db()
    TOKEN=os.getenv('BOT_TOKEN')
    APP_URL=os.getenv('APP_URL')  # https://your-railway-app.up.railway.app
    if not TOKEN or not APP_URL:
        logger.error("BOT_TOKEN or APP_URL env missing")
        return

    app=Application.builder().token(TOKEN).webhook(Webhook(url=f"{APP_URL}/webhook")).build()
    # Handlers
    app.add_handler(CommandHandler('start',start_cmd))
    app.add_handler(CommandHandler('channelid',channel_id_cmd))
    app.add_handler(MessageHandler(filters.TEXT|filters.CAPTION,handle_text))
    app.add_handler(MessageHandler(filters.StatusUpdate.CHANNEL_POST,handle_channel_post))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(InlineQueryHandler(inline_handler))

    # ضبط الـ webhook endpoint
    app.add_webhook(path='/webhook')

    logger.info("Starting webhook...")
    await app.start()
    await app.updater.start_webhook()
    await app.updater.idle()

if __name__=='__main__':
    asyncio.run(main_async())
