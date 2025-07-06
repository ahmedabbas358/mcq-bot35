import os
import re
import logging
import asyncio
import time
from collections import defaultdict, deque
import weakref
import gc
from asyncio import Lock

import aiosqlite
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContentfrom telegram.constants import PollType
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler, filters, ContextTypes
)

# إعداد التسجيل
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# مسار قاعدة البيانات
DB_PATH = 'stats.db'

# هياكل البيانات
send_queues = defaultdict(deque)
last_sent_time = defaultdict(float)
retry_counts = defaultdict(int)
MAX_RETRIES = 3
MAX_QUESTIONS_PER_POST = 3
RATE_LIMIT_DELAY = 2.0

# تعيين الأرقام والحروف العربية
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9', '٠': '0'}
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3, 'هـ': 4, 'و': 5, 'ز': 6, 'ح': 7, 'ط': 8, 'ي': 9}

# أنماط التعبير النمطي
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
        re.S | re.IGNORECASE),
    re.compile(
        r"(?P<q>.+?)\n(?P<opts>(?:[A-D]\)\s*.+?(?:\n|$)){2,4})\s*Answer:\s*(?P<ans>[A-D])",
        re.S
    )
]

# النصوص متعددة اللغات
TEXTS = {
    'start': {'en': '🤖 Hi! Choose an option:', 'ar': '🤖 أهلاً! اختر من القائمة:'},
    'help': {'en': 'Usage:\n- Send MCQ in private.\n- Mention or reply in groups.\n- Formats: Q:/س:', 'ar': '🆘 كيفية الاستخدام:\n- في الخاص أرسل السؤال.\n- في المجموعات اذكر @البوت أو الرد.\n- الصيغ: Q:/س:'},
    'new': {'en': '📩 Send your MCQ now!', 'ar': '📩 أرسل سؤال MCQ الآن!'},
    'stats': {'en': '📊 You sent {sent} questions.\n✉️ Channel posts: {ch}\n📅 Weekly: {weekly}\n📆 Monthly: {monthly}', 'ar': '📊 أرسلت {sent} سؤالاً.\n🏷️ منشورات القناة: {ch}\n📅 أسبوعيًا: {weekly}\n📆 شهريًا: {monthly}'},
    'queue_full': {'en': '🚫 Queue full, send fewer questions.', 'ar': '🚫 القائمة ممتلئة، أرسل أقل.'},
    'no_q': {'en': '❌ No questions detected.', 'ar': '❌ لم أتعرف على أي سؤال.'},
    'error_poll': {'en': '⚠️ Failed to send question. Try again later.', 'ar': '⚠️ فشل في إرسال السؤال. حاول لاحقًا.'},
    'invalid_format': {'en': '⚠️ Please send a properly formatted MCQ.', 'ar': '⚠️ الرجاء إرسال السؤال بصيغة صحيحة.'},
    'sent_by': {'en': 'Sent by [{user}](tg://user?id={user_id})', 'ar': 'أرسل بواسطة [{user}](tg://user?id={user_id})'},
    'awaiting_mcq': {'en': '📝 Please send the MCQ.', 'ar': '📝 الرجاء إرسال السؤال.'},
    'awaiting_channel': {'en': '📡 Please select a channel.', 'ar': '📡 الرجاء اختيار القناة.'},
    'cancel': {'en': '❌ Operation cancelled.', 'ar': '❌ تم إلغاء العملية.'},
    'error': {'en': '⚠️ An error occurred. Please try again.', 'ar': '⚠️ حدث خطأ. الرجاء المحاولة مجددًا.'},
    'top_users': {'en': '🏆 Top Users:\n{users}', 'ar': '🏆 أفضل المستخدمين:\n{users}'},
    'confirm_post': {'en': '📬 Send {count} questions to {channel}?', 'ar': '📬 إرسال {count} أسئلة إلى {channel}؟'},
    'anonymous_setting': {'en': 'Set polls to anonymous? (Current: {current})', 'ar': 'جعل الاستبيانات مجهولة؟ (الحالي: {current})'},
}

# دالة كشف اللغة
def detect_language(text):
    if text and any(c in text for c in 'أبتثجحخدذرزسشصضطظعغفقكلمنهوي'):
        return 'ar'
    return 'en'

# دالة جلب النصوص
def get_text(key, lang, **kwargs):
    return TEXTS[key].get(lang, TEXTS[key]['en']).format(**kwargs)

# فلترة Markdown
def sanitize_markdown(text):
    forbidden = ['[', ']', '*', '_', '`']
    for char in forbidden:
        text = text.replace(char, f'\\{char}')
    return text.strip()

# دوال قاعدة البيانات
async def init_db(db):
    async with db.execute('''CREATE TABLE IF NOT EXISTS user_stats (user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0, last_sent TIMESTAMP, anonymous INTEGER DEFAULT 0)'''):
        pass
    async with db.execute('''CREATE TABLE IF NOT EXISTS channel_stats (chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0, last_sent TIMESTAMP)'''):
        pass
    async with db.execute('''CREATE TABLE IF NOT EXISTS known_channels (chat_id INTEGER PRIMARY KEY, title TEXT)'''):
        pass
    async with db.execute('''CREATE TABLE IF NOT EXISTS processed_messages (msg_key TEXT PRIMARY KEY, timestamp TIMESTAMP)'''):
        pass
    await db.commit()

async def db_execute(db, query, params=(), lock=None):
    async with lock:
        try:
            await db.execute(query, params)
            await db.commit()
        except Exception as e:
            logger.error(f"خطأ في تنفيذ استعلام قاعدة البيانات: {e}")
            raise

async def db_fetchone(db, query, params=(), lock=None):
    async with lock:
        try:
            async with db.execute(query, params) as cursor:
                return await cursor.fetchone()
        except Exception as e:
            logger.error(f"خطأ في جلب بيانات: {e}")
            return None

async def db_fetchall(db, query, params=(), lock=None):
    async with lock:
        try:
            async with db.execute(query, params) as cursor:
                return await cursor.fetchall()
        except Exception as e:
            logger.error(f"خطأ في جلب كل البيانات: {e}")
            return []

async def is_message_processed(db, msg_key, lock=None):
    result = await db_fetchone(db, 'SELECT msg_key FROM processed_messages WHERE msg_key = ?', (msg_key,), lock)
    return result is not None

async def store_processed_message(db, msg_key, lock=None):
    await db_execute(db, 'INSERT OR IGNORE INTO processed_messages (msg_key, timestamp) VALUES (?, ?)', (msg_key, int(time.time())), lock)
    await db_execute(db, 'DELETE FROM processed_messages WHERE timestamp < ?', (int(time.time()) - 24 * 3600,), lock)

# تحليل الأسئلة
def parse_mcq(text, chat_id):
    res = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = sanitize_markdown(m.group('q').strip())
            opts_raw = m.group('opts')
            opts = [sanitize_markdown(opt.strip()) for opt in re.findall(r'^\s*[A-Jأ-ي][).:：-]?\s*(.+)', opts_raw, re.MULTILINE | re.DOTALL)]
            ans = m.group('ans').strip()
            ans = ARABIC_DIGITS.get(ans, ans)
            idx = AR_LETTERS.get(ans, None)
            if idx is None:
                try:
                    idx = int(ans) - 1
                except ValueError:
                    idx = ord(ans.upper()) - ord('A') if ans.isalpha() else None
            if not isinstance(idx, int) or not (0 <= idx < len(opts)):
                continue
            if 2 <= len(opts) <= 10:
                res.append((q, opts, idx))
    return res[:MAX_QUESTIONS_PER_POST]

# معالجة قائمة الانتظار
async def process_queue(chat_id, context):
    q = send_queues[chat_id]
    retry_queue = deque()
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    while q:
        qst, opts, idx, sender_user = q.popleft()
        try:
            if 2 <= len(opts) <= 10 and len(set([idx])) == 1:
                is_anonymous = False
                if sender_user:
                    r = await db_fetchone(db, 'SELECT anonymous FROM user_stats WHERE user_id = ?', (sender_user.id,), lock)
                    is_anonymous = r[0] if r else False
                poll_message = await context.bot.send_poll(
                    chat_id, qst, opts, type=PollType.QUIZ,
                    correct_option_id=idx, is_anonymous=is_anonymous,
                    parse_mode='MarkdownV2'
                )
                if sender_user and not is_anonymous:
                    lang = detect_language(qst)
                    text = get_text('sent_by', lang, user=sender_user.first_name, user_id=sender_user.id)
                    await context.bot.send_message(
                        chat_id, text, reply_to_message_id=poll_message.message_id,
                        parse_mode='MarkdownV2'
                    )
                await asyncio.sleep(min(RATE_LIMIT_DELAY, len(qst) / 50))
                retry_counts[(chat_id, qst)] = 0
            else:
                logger.warning(f"تم تخطي السؤال: خيارات أو إجابة غير صالحة {len(opts)}, {idx}")
        except Exception as e:
            logger.warning(f"فشل في إرسال الاستبيان: {e}")
            if hasattr(e, 'error_code') and e.error_code in (400, 429) and retry_counts[(chat_id, qst)] < MAX_RETRIES:
                retry_counts[(chat_id, qst)] += 1
                retry_queue.append((qst, opts, idx, sender_user))
                await asyncio.sleep(1)
            else:
                lang = detect_language(qst)
                await context.bot.send_message(chat_id, get_text('error_poll', lang))
        finally:
            gc.collect()
    if retry_queue:
        send_queues[chat_id].extend(retry_queue)

# إضافة الأسئلة إلى قائمة الانتظار (المستخدمين)
async def enqueue_user_mcq(message, context, override_chat_id=None):
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    msg_key = f"{message.chat.id}:{message.message_id}"
    if await is_message_processed(db, msg_key, lock):
        return False
    await store_processed_message(db, msg_key, lock)
    
    chat_id = override_chat_id or message.chat.id
    lang = detect_language(message.text or message.caption or '')
    if len(send_queues[chat_id]) > 50:
        await context.bot.send_message(chat_id, get_text('queue_full', lang))
        return False
    text = message.text or message.caption or ''
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    sent = False
    sender_user = message.from_user if message.from_user else None
    for blk in blocks:
        lst = parse_mcq(blk, chat_id)
        for item in lst:
            send_queues[chat_id].append((*item, sender_user))
            sent = True
    if sent:
        try:
            task = asyncio.create_task(process_queue(chat_id, context))
            if 'tasks' not in context.application_data:
                context.application_data['tasks'] = weakref.WeakSet()
            context.application_data['tasks'].add(task)
            task.add_done_callback(lambda t: context.application_data['tasks'].discard(t))
        except Exception as e:
            logger.error(f"فشل في إنشاء مهمة لـ process_queue: {e}")
            await context.bot.send_message(chat_id, get_text('error', lang))
    return sent

# إضافة الأسئلة إلى قائمة الانتظار (القنوات)
async def enqueue_channel_mcq(post, context):
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    msg_key = f"{post.chat.id}:{post.message_id}"
    if await is_message_processed(db, msg_key, lock):
        return False
    await store_processed_message(db, msg_key, lock)
    
    chat_id = post.chat.id
    lang = detect_language(post.text or post.caption or '')
    title = post.chat.title or 'قناة خاصة'
    await db_execute(db, 'INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES(?, ?)', (chat_id, title), lock)
    if len(send_queues[chat_id]) > 50:
        await context.bot.send_message(chat_id, get_text('queue_full', lang))
        return False
    text = post.text or post.caption or ''
    blocks = [b.strip() for b in re.split(r"\n{2,}", text) if b.strip()]
    questions = []
    for blk in blocks:
        questions.extend(parse_mcq(blk, chat_id))
    if len(questions) > MAX_QUESTIONS_PER_POST:
        buttons = [
            [InlineKeyboardButton("✅ تأكيد", callback_data=f"confirm_post_{chat_id}_{len(questions)}")],
            [InlineKeyboardButton("❌ إلغاء", callback_data="cancel_post")]
        ]
        markup = InlineKeyboardMarkup(buttons)
        await context.bot.send_message(chat_id, get_text('confirm_post', lang, count=len(questions), channel=title), reply_markup=markup)
        context.bot_data[f"pending_{chat_id}"] = questions
        return False
    sent = False
    for item in questions:
        send_queues[chat_id].append((*item, None))
        sent = True
    if sent:
        try:
            task = asyncio.create_task(process_queue(chat_id, context))
            if 'tasks' not in context.application_data:
                context.application_data['tasks'] = weakref.WeakSet()
            context.application_data['tasks'].add(task)
            task.add_done_callback(lambda t: context.application_data['tasks'].discard(t))
        except Exception as e:
            logger.error(f"فشل في إنشاء مهمة لـ process_queue: {e}")
            await context.bot.send_message(chat_id, get_text('error', lang))
    return sent

# معالجة النصوص
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or (not msg.text and not msg.caption):
        return
    uid = msg.from_user.id
    ct = msg.chat.type
    lang = detect_language(msg.text or msg.caption or '')
    
    if time.time() - last_sent_time[uid] < 5:
        return
    last_sent_time[uid] = time.time()

    state = context.user_data.get('state')
    if ct == 'private' and state == 'waiting_mcq':
        if await enqueue_user_mcq(msg, context, override_chat_id=context.user_data.get('target_channel')):
            context.user_data['state'] = 'waiting_channel'
            rows = await db_fetchall(context.bot_data.get('db'), 'SELECT chat_id,title FROM known_channels', lock=context.bot_data.get('db_lock'))
            if not rows:
                await msg.reply_text("❌ لم يتم العثور على قنوات.")
                context.user_data.pop('state', None)
                context.user_data.pop('target_channel', None)
                return
            buttons = [[InlineKeyboardButton(t, callback_data=f'choose_ch_{cid}')] for cid, t in rows]
            markup = InlineKeyboardMarkup(buttons)
            await msg.reply_text(get_text('awaiting_channel', lang), reply_markup=markup)
            try:
                await msg.delete()
            except Exception as e:
                logger.warning(f"فشل الحذف: {e}")
            return
        else:
            await msg.reply_text(get_text('no_q', lang))
            return

    if ct == 'private':
        target_channel = context.user_data.get('target_channel')
        target = target_channel if target_channel else msg.chat.id
        if await enqueue_user_mcq(msg, context, override_chat_id=target):
            current_time = int(time.time())
            db = context.bot_data.get('db')
            lock = context.bot_data.get('db_lock')
            if not target_channel:
                await db_execute(db, 'INSERT OR IGNORE INTO user_stats(user_id, sent, last_sent, anonymous) VALUES (?, 0, ?, 0)', (uid, current_time), lock)
                await db_execute(db, 'UPDATE user_stats SET sent = COALESCE(sent, 0) + ?, last_sent = ? WHERE user_id = ?', (len(send_queues[msg.chat.id]), current_time, uid), lock)
            else:
                await db_execute(db, 'INSERT OR IGNORE INTO channel_stats(chat_id, sent, last_sent) VALUES (?, 0, ?)', (target, current_time), lock)
                await db_execute(db, 'UPDATE channel_stats SET sent = COALESCE(sent, 0) + ?, last_sent = ? WHERE chat_id = ?', (len(send_queues[target]), current_time, target), lock)
            try:
                await msg.delete()
            except Exception as e:
                logger.warning(f"فشل الحذف: {e}")
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
        if await enqueue_user_mcq(msg, context):
            try:
                await msg.delete()
            except Exception as e:
                logger.warning(f"فشل الحذف: {e}")
        else:
            await context.bot.send_message(msg.chat.id, get_text('invalid_format', lang))

# معالجة منشورات القنوات
async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    post = update.channel_post
    if not post:
        return
    botun = (context.bot.username or '').lower()
    text = post.text or post.caption or ''
    if f"@{botun}" not in text.lower():
        return
    if await enqueue_channel_mcq(post, context):
        try:
            await post.delete()
        except Exception as e:
            if hasattr(e, 'error_code') and e.error_code in (400, 403):
                logger.warning(f"فشل الحذف بسبب نقص الصلاحيات: {e}")
            else:
                logger.error(f"فشل الحذف: {e}")

# الأوامر
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = detect_language(update.message.text or '') if update.message else 'en'
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📝 سؤال جديد', callback_data='new')],
        [InlineKeyboardButton('🔄 نشر في قناة', callback_data='publish_channel')],
        [InlineKeyboardButton('📊 إحصائياتي', callback_data='stats')],
        [InlineKeyboardButton('📘 المساعدة', callback_data='help')],
        [InlineKeyboardButton('📺 القنوات', callback_data='channels')],
        [InlineKeyboardButton('🏆 أفضل المستخدمين', callback_data='top_users')],
        [InlineKeyboardButton('🔒 إعدادات الخصوصية', callback_data='anonymous_setting')]
    ])
    await update.message.reply_text(get_text('start', lang), reply_markup=kb)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmd = update.callback_query.data
    uid = update.effective_user.id
    lang = detect_language(update.callback_query.message.text or '') if update.callback_query.message else 'en'
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    if cmd == 'help':
        txt = get_text('help', lang)
    elif cmd == 'new':
        context.user_data['state'] = 'waiting_mcq'
        txt = get_text('awaiting_mcq', lang)
    elif cmd == 'stats':
        r = await db_fetchone(db, 'SELECT sent, last_sent FROM user_stats WHERE user_id=?', (uid,), lock)
        sent, last_sent = (r[0], r[1]) if r else (0, 0)
        r = await db_fetchone(db, 'SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,), lock)
        ch = r[0] if r else 0
        weekly = 0
        monthly = 0
        if last_sent:
            week_ago = int(time.time()) - 7 * 24 * 3600
            month_ago = int(time.time()) - 30 * 24 * 3600
            weekly = sent if last_sent >= week_ago else 0
            monthly = sent if last_sent >= month_ago else 0
        txt = get_text('stats', lang, sent=sent, ch=ch, weekly=weekly, monthly=monthly)
    elif cmd == 'channels':
        rows = await db_fetchall(db, 'SELECT chat_id,title FROM known_channels', lock)
        txt = "📡 القنوات:\n" + "\n".join([f"- {title}: {channel_id}" for channel_id, title in rows]) if rows else "لا توجد قنوات"
    elif cmd == 'top_users':
        rows = await db_fetchall(db, 'SELECT user_id, sent FROM user_stats ORDER BY sent DESC LIMIT 5', lock)
        users = "\n".join([f"- User {uid}: {sent} questions" for uid, sent in rows]) if rows else "لا توجد إحصائيات"
        txt = get_text('top_users', lang, users=users)
    elif cmd == 'publish_channel':
        context.user_data['state'] = 'waiting_mcq'
        rows = await db_fetchall(db, 'SELECT chat_id,title FROM known_channels', lock)
        if not rows:
            await update.callback_query.edit_message_text("❌ لم يتم العثور على قنوات.")
            return
        buttons = [[InlineKeyboardButton(t, callback_data=f'choose_ch_{cid}')] for cid, t in rows]
        markup = InlineKeyboardMarkup(buttons)
        await update.callback_query.edit_message_text("📡 اختر القناة للنشر:", reply_markup=markup)
        return
    elif cmd.startswith('choose_ch_'):
        ch_id = int(cmd.replace('choose_ch_', ''))
        title = await db_fetchone(db, 'SELECT title FROM known_channels WHERE chat_id=?', (ch_id,), lock)
        if not title:
            await update.callback_query.edit_message_text("❌ القناة غير موجودة.")
            return
        context.user_data['target_channel'] = ch_id
        context.user_data.pop('state', None)
        await update.callback_query.edit_message_text(f"✅ تم تحديد القناة: {title[0]}\n📩 أرسل الآن السؤال وسينشر هناك.")
        return
    elif cmd.startswith('confirm_post_'):
        try:
            _, chat_id, count = cmd.split('_')
            chat_id, count = int(chat_id), int(count)
        except ValueError:
            await update.callback_query.edit_message_text("❌ خطأ في معالجة الأمر.")
            return
        questions = context.bot_data.pop(f"pending_{chat_id}", [])
        if not questions:
            await update.callback_query.edit_message_text("❌ لا توجد أسئلة معلقة.")
            return
        for qst, opts, idx in questions:
            send_queues[chat_id].append((qst, opts, idx, None))
        try:
            task = asyncio.create_task(process_queue(chat_id, context))
            if 'tasks' not in context.application_data:
                context.application_data['tasks'] = weakref.WeakSet()
            context.application_data['tasks'].add(task)
            task.add_done_callback(lambda t: context.application_data['tasks'].discard(t))
        except Exception as e:
            logger.error(f"فشل في إنشاء مهمة لـ process_queue: {e}")
            await context.bot.send_message(chat_id, get_text('error', lang))
        await update.callback_query.edit_message_text(f"✅ تم إرسال {count} أسئلة.")
        return
    elif cmd == 'cancel_post':
        context.bot_data.pop(f"pending_{update.effective_chat.id}", None)
        await update.callback_query.edit_message_text("❌ تم إلغاء النشر.")
        return
    elif cmd == 'anonymous_setting':
        r = await db_fetchone(db, 'SELECT anonymous FROM user_stats WHERE user_id = ?', (uid,), lock)
        current = r[0] if r else 0
        buttons = [
            [InlineKeyboardButton("✅ مجهول", callback_data='set_anonymous_1')],
            [InlineKeyboardButton("❌ غير مجهول", callback_data='set_anonymous_0')],
            [InlineKeyboardButton("🔙 رجوع", callback_data='start')]
        ]
        markup = InlineKeyboardMarkup(buttons)
        await update.callback_query.edit_message_text(
            get_text('anonymous_setting', lang, current='مجهول' if current else 'غير مجهول'),
            reply_markup=markup
        )
        return
    elif cmd.startswith('set_anonymous_'):
        value = int(cmd.replace('set_anonymous_', ''))
        await db_execute(db, 'INSERT OR REPLACE INTO user_stats (user_id, sent, last_sent, anonymous) VALUES (?, COALESCE((SELECT sent FROM user_stats WHERE user_id = ?), 0), COALESCE((SELECT last_sent FROM user_stats WHERE user_id = ?), 0), ?)', (uid, uid, uid, value), lock)
        await update.callback_query.edit_message_text(f"✅ تم تحديد الاستبيانات إلى {'مجهول' if value else 'غير مجهول'}")
        return
    else:
        txt = '⚠️ غير مدعوم'
    await update.callback_query.edit_message_text(txt, parse_mode='MarkdownV2')

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = update.inline_query.query
        if not q:
            return
        lang = detect_language(q)
        match = parse_mcq(q, 0)
        if match:
            qst, opts, idx = match[0]
            if 2 <= len(opts) <= 10 and 0 <= idx < len(opts):
                res_text = f"*{sanitize_markdown(qst)}*\n" + "\n".join([f"{chr(65+i)}) {opt}{' ✅' if i == idx else ''}" for i, opt in enumerate(opts)])
                results = [InlineQueryResultArticle(id='1', title='عرض السؤال', input_message_content=InputTextMessageContent(res_text, parse_mode='MarkdownV2'))]
                await update.inline_query.answer(results)
            else:
                results = [InlineQueryResultArticle(id='1', title='تحويل سؤال MCQ', input_message_content=InputTextMessageContent(get_text('invalid_format', lang)))]
                await update.inline_query.answer(results)
        else:
            results = [InlineQueryResultArticle(id='1', title='تحويل سؤال MCQ', input_message_content=InputTextMessageContent(get_text('no_q', lang)))]
            await update.inline_query.answer(results)
    except Exception as e:
        logger.error(f"خطأ في الاستعلام المضمن: {e}")
        lang = 'en'
        results = [InlineQueryResultArticle(id='1', title='خطأ', input_message_content=InputTextMessageContent(get_text('error', lang)))]
        await update.inline_query.answer(results)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = detect_language(update.message.text or '') if update.message else 'en'
    await update.message.reply_text(get_text('help', lang))

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    lang = detect_language(update.message.text or '') if update.message else 'en'
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    r = await db_fetchone(db, 'SELECT sent, last_sent FROM user_stats WHERE user_id=?', (uid,), lock)
    sent, last_sent = (r[0], r[1]) if r else (0, 0)
    r = await db_fetchone(db, 'SELECT sent FROM channel_stats WHERE chat_id=?', (update.effective_chat.id,), lock)
    ch = r[0] if r else 0
    weekly = 0
    monthly = 0
    if last_sent:
        week_ago = int(time.time()) - 7 * 24 * 3600
        month_ago = int(time.time()) - 30 * 24 * 3600
        weekly = sent if last_sent >= week_ago else 0
        monthly = sent if last_sent >= month_ago else 0
    await update.message.reply_text(get_text('stats', lang, sent=sent, ch=ch, weekly=weekly, monthly=monthly))

async def channels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = detect_language(update.message.text or '') if update.message else 'en'
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    rows = await db_fetchall(db, 'SELECT chat_id,title FROM known_channels', lock)
    if not rows:
        await update.message.reply_text("لا توجد قنوات مسجلة بعد.")
        return
    text = "📡 القنوات المسجلة:\n" + "\n".join([f"- {title}: {channel_id}" for channel_id, title in rows])
    await update.message.reply_text(text, parse_mode='MarkdownV2')

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = detect_language(update.message.text or '') if update.message else 'en'
    context.user_data.pop('state', None)
    context.user_data.pop('target_channel', None)
    await update.message.reply_text(get_text('cancel', lang))

async def new_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = detect_language(update.message.text or '') if update.message else 'en'
    context.user_data['state'] = 'waiting_mcq'
    await update.message.reply_text(get_text('awaiting_mcq', lang))

async def publish_channel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = detect_language(update.message.text or '') if update.message else 'en'
    context.user_data['state'] = 'waiting_mcq'
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    rows = await db_fetchall(db, 'SELECT chat_id,title FROM known_channels', lock)
    if not rows:
        await update.message.reply_text("❌ لم يتم العثور على قنوات.")
        return
    buttons = [[InlineKeyboardButton(t, callback_data=f'choose_ch_{cid}')] for cid, t in rows]
    markup = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("📡 اختر القناة للنشر:", reply_markup=markup)

async def top_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = detect_language(update.message.text or '') if update.message else 'en'
    db = context.bot_data.get('db')
    lock = context.bot_data.get('db_lock')
    rows = await db_fetchall(db, 'SELECT user_id, sent FROM user_stats ORDER BY sent DESC LIMIT 5', lock)
    users = "\n".join([f"- User {uid}: {sent} questions" for uid, sent in rows]) if rows else "لا توجد إحصائيات"
    await update.message.reply_text(get_text('top_users', lang, users=users))

async def cleanup_db(db, lock):
    try:
        await db_execute(db, """
            DELETE FROM known_channels
            WHERE chat_id NOT IN (
                SELECT chat_id FROM channel_stats WHERE sent > 0
            )
            AND rowid IN (
                SELECT rowid FROM known_channels LIMIT 1000
            )
        """, lock=lock)
        await db_execute(db, "DELETE FROM user_stats WHERE sent = 0", lock=lock)
        await db_execute(db, "DELETE FROM processed_messages WHERE timestamp < ?", (int(time.time()) - 24 * 3600,), lock=lock)
    except Exception as e:
        logger.error(f"خطأ في تنظيف قاعدة البيانات: {e}")

async def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        raise RuntimeError("❌ لم يتم تحديد توكن البوت.")
    
    # إنشاء التطبيق
    app = Application.builder().token(token).build()

    # تهيئة قاعدة البيانات
    db = await aiosqlite.connect(DB_PATH)
    app.bot_data['db'] = db
    app.bot_data['db_lock'] = Lock()
    await init_db(db)

    # إضافة المعالجات
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('stats', stats_command))
    app.add_handler(CommandHandler('channels', channels_command))
    app.add_handler(CommandHandler('cancel', cancel_command))
    app.add_handler(CommandHandler('new', new_command))
    app.add_handler(CommandHandler('publish_channel', publish_channel_command))
    app.add_handler(CommandHandler('top_users', top_users_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.CAPTION), handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # تشغيل البوت
    try:
        logger.info('✅ البوت يعمل...')
        # تهيئة التطبيق
        await app.initialize()
        # تشغيل البوت في وضع polling
        await app.run_polling()
    except Exception as e:
        logger.error(f"خطأ في تشغيل البوت: {e}")
    finally:
        # إغلاق التطبيق وقاعدة البيانات
        try:
            await app.shutdown()
            await cleanup_db(db, app.bot_data['db_lock'])
            await db.close()
        except Exception as e:
            logger.error(f"خطأ أثناء الإغلاق: {e}")
        finally:
            gc.collect()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # إذا كانت الحلقة قيد التشغيل، استخدمها مباشرة
        loop.create_task(main())
    else:
        # إذا لم تكن الحلقة قيد التشغيل، شغلها
        loop.run_until_complete(main())
