import os
import re
import logging
import asyncio
import time
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = 'stats.db'

send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)

ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9', '٠': '0'}
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3, 'هـ': 4, 'و': 5, 'ز': 6, 'ح': 7, 'ط': 8, 'ي': 9}

# تحسين regex لتقبل خيارات حتى في سطر جديد أو بدون علامات
PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[A-Jأ-ي][).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j0-9١-٩])",
        re.S | re.IGNORECASE),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*(?P<opts>(?:[أ-ي][).:]\s*.+?(?:\n|$)){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-ي١-٩])",
        re.S),
    re.compile(
        r"(?P<q>.+?)\n(?P<opts>(?:\s*[A-Za-zء-ي0-9]+[).:]\s*.+?(?:\n|$)){2,10})"
        r"(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9١-٩])",
        re.S | re.IGNORECASE)
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

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT)''')
        await db.commit()

async def db_execute(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()

async def db_fetchone(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchone()

async def db_fetchall(query, params=()):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cursor:
            return await cursor.fetchall()

def parse_mcq(text, chat_id):
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            # مرونة أكبر في استخراج الخيارات (أخذ السطر بعد العلامة حتى نهاية السطر)
            opts_raw = m.group('opts')
            opts = re.findall(r'[A-Jأ-ي][).:]\s*(.+)', opts_raw)
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
            if 2 <= len(opts) <= 10:
                await context.bot.send_poll(chat_id, qst, opts, type=Poll.QUIZ, correct_option_id=idx, is_anonymous=False)
                await asyncio.sleep(0.5)
            else:
                logger.warning(f"Question skipped due to invalid number of options: {len(opts)}")
        except Exception as e:
            logger.warning(f"Failed to send poll: {e}")

async def enqueue_mcq(message, context, override_chat_id=None):
    chat_id = override_chat_id or message.chat.id
    if message.chat.type == 'channel':
        title = message.chat.title or 'Private Channel'
        await db_execute('INSERT OR IGNORE INTO known_channels(chat_id,title) VALUES(?,?)', (chat_id, title))
    if len(send_queues[chat_id]) > 50:
        lang = (getattr(message.from_user, 'language_code', 'en') or 'en')[:2]
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
        try:
            asyncio.create_task(process_queue(chat_id, context))
        except Exception as e:
            logger.error(f"Failed to create task for process_queue: {e}")
    return sent

async def handle_text(update, context):
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return
    uid = msg.from_user.id
    ct = msg.chat.type
    lang = (getattr(msg.from_user, 'language_code', 'en') or 'en')[:2]
    if time.time() - last_sent_time[uid] < 5:
        return
    last_sent_time[uid] = time.time()

    if ct == 'private':
        target_channel = context.user_data.get('target_channel')
        target = target_channel if target_channel else msg.chat.id
        if await enqueue_mcq(msg, context, override_chat_id=target):
            if not target_channel:
                # استخدم جملة واحدة لتجنب تعارض التحديث
                await db_execute('INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES (?, 0)', (uid,))
                await db_execute('UPDATE user_stats SET sent = COALESCE(sent, 0) + ? WHERE user_id = ?', (len(send_queues[msg.chat.id]), uid))
            else:
                await db_execute('INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?, 0)', (target,))
                await db_execute('UPDATE channel_stats SET sent = COALESCE(sent, 0) + ? WHERE chat_id = ?', (len(send_queues[target]), target))
            try:
                await msg.delete()
            except Exception:
                logger.warning("Failed to delete message", exc_info=True)
            if target_channel:
                context.user_data.pop('target_channel', None)
        else:
            await context.bot.send_message(msg.chat.id, get_text('no_q', lang))
        return

    botun = (context.bot.username or '').lower()
    if ct in ['group', 'supergroup'] and (
        (msg.reply_to_message and msg.reply_to_message.from_user.id == context.bot.id) or
        (botun and f"@{botun}" in (msg.text or msg.caption or '').lower())
    ):
        if not await enqueue_mcq(msg, context):
            await context.bot.send_message(msg.chat.id, get_text('invalid_format', lang))

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
    await db_execute('INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES(?, ?)', (chat_id, title))
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    total_sent = 0
    for blk in blocks:
        lst = parse_mcq(blk, chat_id)
        for q, opts, idx in lst:
            try:
                if 2 <= len(opts) <= 10:
                    await context.bot.send_poll(
                        chat_id=chat_id,
                        question=q,
                        options=opts,
                        type=Poll.QUIZ,
                        correct_option_id=idx,
                        is_anonymous=False
                    )
                    await asyncio.sleep(0.5)
                    total_sent += 1
                else:
                    logger.warning(f"Skipped poll in channel post: invalid options count {len(opts)}")
            except Exception as e:
                logger.warning(f"❌ فشل في إرسال استبيان إلى القناة {chat_id}: {e}")
    if total_sent > 0:
        await db_execute('INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?, 0)', (chat_id,))
        await db_execute('UPDATE channel_stats SET sent = COALESCE(sent, 0) + ? WHERE chat_id = ?', (total_sent, chat_id))

async def start(update, context):
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('🔄 نشر في قناة', callback_data='publish_channel')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')],
        [InlineKeyboardButton('📺 القنوات', callback_data='channels')]
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def callback_query_handler(update, context):
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        txt = get_text('new', lang)
    elif cmd == 'stats':
        r = await db_fetchone('SELECT sent FROM user_stats WHERE user_id=?', (uid,))
        sent = r[0] if r else 0
        r = await db_fetchone('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,))
        ch = r[0] if r else 0
        txt = get_text('stats', lang).format(sent=sent, ch=ch)
    elif cmd == 'channels':
        rows = await db_fetchall('SELECT chat_id,title FROM known_channels')
        txt = "لا توجد قنوات" if not rows else "\n".join([f"- {t}: {cid}" for cid,t in rows])
    elif cmd == 'publish_channel':
        rows = await db_fetchall('SELECT chat_id,title FROM known_channels')
        if not rows:
            await update.callback_query.edit_message_text("❌ لم يتم العثور على قنوات.")
            return
        buttons = [[InlineKeyboardButton(t, callback_data=f'choose_ch_{cid}')] for cid, t in rows]
        markup = InlineKeyboardMarkup(buttons)
        await update.callback_query.edit_message_text("📡 اختر القناة للنشر:", reply_markup=markup)
        return
    elif cmd.startswith('choose_ch_'):
        ch_id = int(cmd.replace('choose_ch_', ''))
        title = await db_fetchone('SELECT title FROM known_channels WHERE chat_id=?', (ch_id,))
        if not title:
            await update.callback_query.edit_message_text("❌ القناة غير موجودة.")
            return
        context.user_data['target_channel'] = ch_id
        await update.callback_query.edit_message_text(f"✅ تم تحديد القناة: {title[0]}\n📩 أرسل الآن السؤال وسينشر هناك.")
        return
    else:
        txt = '⚠️ غير مدعوم'
    await update.callback_query.edit_message_text(txt, parse_mode='Markdown')

async def inline_query(update, context):
    try:
        q = update.inline_query.query
        if not q:
            return
        results = [InlineQueryResultArticle(id='1', title='تحويل سؤال MCQ', input_message_content=InputTextMessageContent(q))]
        await update.inline_query.answer(results)
    except Exception as e:
        logger.error(f"Inline error: {e}")

async def help_command(update, context):
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    await update.message.reply_text(get_text('help', lang))

async def stats_command(update, context):
    uid = update.effective_user.id
    lang = (getattr(update.effective_user, 'language_code', 'en') or 'en')[:2]
    r = await db_fetchone('SELECT sent FROM user_stats WHERE user_id=?', (uid,))
    sent = r[0] if r else 0
    r = await db_fetchone('SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,))
    ch = r[0] if r else 0
    await update.message.reply_text(get_text('stats', lang).format(sent=sent, ch=ch))

async def channels_command(update, context):
    rows = await db_fetchall('SELECT chat_id,title FROM known_channels')
    if not rows:
        await update.message.reply_text("لا توجد قنوات مسجلة بعد.")
        return
    text = "📡 القنوات المسجلة:\n" + "\n".join([f"- {t}: {cid}" for cid,t in rows])
    await update.message.reply_text(text, parse_mode='Markdown')

# مثال على دالة تنظيف دورية (يمكن استدعاؤها من مهمة مجدولة في الخلفية)
async def cleanup_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # حذف القنوات غير النشطة منذ 6 أشهر (تحتاج تعديل حسب الحاجة)
        await db.execute("""
            DELETE FROM known_channels 
            WHERE chat_id NOT IN (
                SELECT chat_id FROM channel_stats WHERE sent > 0
            )
            AND rowid IN (
                SELECT rowid FROM known_channels LIMIT 1000
            )
        """)
        # حذف المستخدمين الذين لم يرسلوا شيئاً منذ فترة طويلة (مثال)
        await db.execute("""
            DELETE FROM user_stats WHERE sent = 0
        """)
        await db.commit()

def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError("❌ لم يتم تحديد توكن البوت.")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('stats', stats_command))
    app.add_handler(CommandHandler('channels', channels_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption()), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # تأكد من إنشاء الجداول قبل البدء
    asyncio.get_event_loop().run_until_complete(init_db())

    logger.info('✅ Bot is running...')
    app.run_polling()

if __name__ == '__main__':
    main()
