import os
import re
import logging
import random
import asyncio
import aiosqlite
from collections import defaultdict
from telegram import (
    Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_PATH = 'stats.db'

ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9', '٠': '0'}
AR_LETTERS = {
    'أ': 0, 'ب': 1, 'ج': 2, 'د': 3, 'هـ': 4, 'و': 5, 'ز': 6, 'ح': 7, 'ط': 8, 'ي': 9,
    'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7, 'I': 8, 'J': 9
}

PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[A-J][).:]\s*.+?\s*){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j1-9١-٩])",
        re.S | re.IGNORECASE
    ),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[أ-ي][).:]\s*.+?\s*){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-ي1-9١-٩])",
        re.S
    ),
    re.compile(
        r"(?P<q>.+?)\n"
        r"(?P<opts>(?:\s*[A-Za-zء-ي0-9]+[).:]\s*.+?\n){2,10})"
        r"(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي0-9١-٩])",
        re.S | re.IGNORECASE
    ),
]

TEXTS = {
    'start': {'en': '🤖 Hi! Send your MCQ now or use the buttons below.',
              'ar': '🤖 أهلاً! أرسل سؤالك بصيغة MCQ أو استخدم الأزرار أدناه.'},
    'help': {'en': 'Usage:\n/broadcast - Broadcast MCQ to your selected channels\n/addchannel - Register a channel\n/delchannel - Delete a channel\n/listchannels - List your channels\n/help - Show help',
             'ar': '🆘 استخدم:\n/broadcast - بث السؤال لقنواتك المختارة\n/addchannel - تسجيل قناة\n/delchannel - حذف قناة\n/listchannels - عرض قنواتك\n/help - عرض المساعدة'},
    'no_channels': {'en': '❌ You have no registered channels. Use /addchannel first.',
                    'ar': '❌ لم تضف أي قناة بعد. استخدم /addchannel أولاً.'},
    'select_channels': {'en': '📢 Select channels to broadcast to (tap buttons):',
                        'ar': '📢 اختر القنوات للبث إليها (اضغط الأزرار):'},
    'choose_channels_empty': {'en': '⚠️ You must select at least one channel.',
                             'ar': '⚠️ يجب اختيار قناة واحدة على الأقل.'},
    'send_mcq_after_select': {'en': '📩 Now send the MCQ question to broadcast.',
                             'ar': '📩 الآن أرسل سؤال MCQ ليتم بثه.'},
    'broadcast_success': {'en': '✅ Question broadcasted to selected channels.',
                          'ar': '✅ تم بث السؤال على القنوات المختارة.'},
    'invalid_mcq': {'en': '❌ Invalid MCQ format, please try again.',
                    'ar': '❌ صيغة السؤال غير صحيحة، حاول مرة أخرى.'},
    'error_sending_poll': {'en': '⚠️ Error sending poll.',
                          'ar': '⚠️ حدث خطأ أثناء إرسال السؤال.'},
    'channel_added': {'en': '✅ Channel added successfully.',
                      'ar': '✅ تمت إضافة القناة بنجاح.'},
    'channel_add_fail': {'en': '❌ Failed to add channel. Ensure bot is admin in the channel.',
                        'ar': '❌ فشل إضافة القناة. تحقق أن البوت مشرف في القناة.'},
    'channel_not_found': {'en': '❌ Channel not found or not registered.',
                         'ar': '❌ القناة غير موجودة أو غير مسجلة.'},
    'channel_deleted': {'en': '✅ Channel deleted.',
                        'ar': '✅ تم حذف القناة.'},
    'no_channels_to_delete': {'en': '❌ You have no channels to delete.',
                             'ar': '❌ لا توجد قنوات لحذفها.'},
    'your_channels': {'en': '📋 Your registered channels:\n{channels}',
                      'ar': '📋 قنواتك المسجلة:\n{channels}'},
}

def get_text(key: str, lang: str = 'en') -> str:
    return TEXTS.get(key, {}).get(lang, TEXTS[key]['en'])

def get_lang(update: Update) -> str:
    return (update.effective_user.language_code or 'en')[:2]

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

def parse_mcq(text: str):
    for patt in PATTERNS:
        m = patt.search(text)
        if not m:
            continue

        q = m.group('q').strip()
        lines = m.group('opts').strip().splitlines()
        opts = []

        for line in lines:
            parts = re.split(r"^[A-Jأ-يA-Za-zء-ي0-9][).:]\s*", line.strip(), maxsplit=1)
            if len(parts) == 2:
                opts.append(parts[1].strip())

        raw_ans = m.group('ans').strip()
        ans = ARABIC_DIGITS.get(raw_ans, raw_ans)

        try:
            if ans.isdigit():
                idx = int(ans) - 1
            elif ans.upper() in AR_LETTERS:
                idx = AR_LETTERS[ans.upper()]
            else:
                return []
        except Exception:
            return []

        if 0 <= idx < len(opts):
            pairs = list(enumerate(opts))
            random.shuffle(pairs)
            shuffled = [opt for _, opt in pairs]
            new_idx = next(i for i, (orig, _) in enumerate(pairs) if orig == idx)
            return [(q, shuffled, new_idx)]
    return []

# حفظ قنوات المستخدم والانتظار للبث
user_selected_channels = defaultdict(list)
user_broadcast_state = defaultdict(bool)

# --- أوامر إدارة القنوات ---

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update)
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("❗️ أرسل معرف القناة (مثل: @channelusername) بعد الأمر.")
        return

    channel_username = context.args[0]
    try:
        chat = await context.bot.get_chat(channel_username)
        # تأكد أن البوت مشرف في القناة
        member = await chat.get_member(context.bot.id)
        if not (member.status in ['administrator', 'creator']):
            await update.message.reply_text(get_text('channel_add_fail', lang))
            return

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)', (chat.id, chat.title or ""))
            await db.execute('INSERT OR IGNORE INTO user_channels(user_id, chat_id) VALUES (?, ?)', (user_id, chat.id))
            await db.commit()
        await update.message.reply_text(get_text('channel_added', lang))
    except Exception as e:
        logger.error(f"Error adding channel: {e}")
        await update.message.reply_text(get_text('channel_add_fail', lang))

async def del_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update)
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text("❗️ أرسل معرف القناة بعد الأمر.")
        return

    channel_username = context.args[0]
    try:
        chat = await context.bot.get_chat(channel_username)
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('DELETE FROM user_channels WHERE user_id = ? AND chat_id = ?', (user_id, chat.id))
            await db.commit()
            if cursor.rowcount == 0:
                await update.message.reply_text(get_text('channel_not_found', lang))
                return
        await update.message.reply_text(get_text('channel_deleted', lang))
    except Exception as e:
        logger.error(f"Error deleting channel: {e}")
        await update.message.reply_text(get_text('channel_not_found', lang))

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update)
    user_id = update.effective_user.id

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall('''
            SELECT chat_id, title FROM user_channels
            JOIN known_channels USING(chat_id)
            WHERE user_id = ?
        ''', (user_id,))

    if not rows:
        await update.message.reply_text(get_text('no_channels', lang))
        return

    lines = [f"- {title} (`{chat_id}`)" for chat_id, title in rows]
    text = get_text('your_channels', lang).format(channels="\n".join(lines))
    await update.message.reply_text(text)

# --- نفس باقي الأوامر من النسخة السابقة ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update)
    kb = [
        [InlineKeyboardButton("🆘 Help / مساعدة", callback_data="help")],
        [InlineKeyboardButton("📢 Broadcast / بث", callback_data="broadcast")]
    ]
    await update.message.reply_text(
        get_text('start', lang),
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update)
    await update.message.reply_text(get_text('help', lang))

async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(update)

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute_fetchall('''
            SELECT chat_id, title FROM user_channels
            JOIN known_channels USING(chat_id)
            WHERE user_id = ?
        ''', (user_id,))

    if not rows:
        await update.message.reply_text(get_text('no_channels', lang))
        return

    buttons = [
        [InlineKeyboardButton(title, callback_data=f"select_channel:{chat_id}")]
        for chat_id, title in rows
    ]
    buttons.append([InlineKeyboardButton("✅ Finish selection / تم الاختيار", callback_data="finish_selection")])

    user_selected_channels[user_id] = []
    user_broadcast_state[user_id] = False

    await update.message.reply_text(
        get_text('select_channels', lang),
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    lang = get_lang(update)
    data = query.data

    if data.startswith("select_channel:"):
        chat_id = int(data.split(":")[1])
        selected = user_selected_channels.get(user_id, [])

        if chat_id in selected:
            selected.remove(chat_id)
            await query.answer("Removed from selection / تم الإزالة")
        else:
            selected.append(chat_id)
            await query.answer("Added to selection / تم الإضافة")

        user_selected_channels[user_id] = selected

    elif data == "finish_selection":
        selected = user_selected_channels.get(user_id, [])
        if not selected:
            await query.answer(get_text('choose_channels_empty', lang), show_alert=True)
            return

        user_broadcast_state[user_id] = True
        await query.edit_message_text(get_text('send_mcq_after_select', lang))
        await query.answer()

    elif data == "help":
        await query.edit_message_text(
            "✅ Supported formats:\n"
            "Q: What is the capital of France?\n"
            "A) Berlin\nB) Paris\nC) Madrid\nD) Rome\nAnswer: B\n\n"
            "س: ما هي عاصمة مصر؟\n"
            "أ) الخرطوم\nب) القاهرة\nج) الرياض\nد) تونس\nالإجابة الصحيحة: ب"
        )
        await query.answer()

    elif data == "broadcast":
        await broadcast_handler(update, context)
        await query.answer()

    else:
        await query.answer("⚠️ Unsupported command")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lang = get_lang(update)

    if user_broadcast_state.get(user_id):
        text = update.message.text
        mcqs = parse_mcq(text)
        if not mcqs:
            await update.message.reply_text(get_text('invalid_mcq', lang))
            return

        q, opts, idx = mcqs[0]
        selected_channels = user_selected_channels.get(user_id, [])

        for ch_id in selected_channels:
            try:
                await context.bot.send_poll(
                    chat_id=ch_id,
                    question=q,
                    options=opts,
                    type=Poll.QUIZ,
                    correct_option_id=idx,
                    is_anonymous=False,
                    protect_content=True,
                )
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error broadcasting to {ch_id}: {e}")

        await update.message.reply_text(get_text('broadcast_success', lang))

        user_broadcast_state[user_id] = False
        user_selected_channels[user_id] = []
        return

    text = update.message.text
    blocks = [blk.strip() for blk in re.split(r"\n{2,}", text) if blk.strip()]
    sent = False

    for blk in blocks:
        mcqs = parse_mcq(blk)
        if not mcqs:
            continue

        sent = True
        for question, opts, correct in mcqs:
            if not 2 <= len(opts) <= 10:
                await update.message.reply_text("❌ عدد الاختيارات يجب أن يكون بين 2 و10.")
                continue
            try:
                await context.bot.send_poll(
                    chat_id=update.effective_chat.id,
                    question=question,
                    options=opts,
                    type=Poll.QUIZ,
                    correct_option_id=correct,
                    is_anonymous=False,
                    protect_content=True,
                )
                kb = [[InlineKeyboardButton("👈 سؤال جديد", callback_data="new")]]
                await update.message.reply_text(
                    "هل تريد إرسال سؤال آخر؟", reply_markup=InlineKeyboardMarkup(kb)
                )
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error sending poll: {e}")
                await update.message.reply_text(get_text('error_sending_poll', lang))

    if not sent:
        buttons = [
            [InlineKeyboardButton("📝 مثال MCQ", callback_data="example")],
            [InlineKeyboardButton("📘 كيف أصيغ السؤال؟", callback_data="help")]
        ]
        await update.message.reply_text(
            "❌ لم أتعرف على أي سؤال. استخدم أحد الصيغ المدعومة.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def main():
    await init_db()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN is not set.")
        return

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CommandHandler('broadcast', broadcast_handler))
    app.add_handler(CommandHandler('addchannel', add_channel))
    app.add_handler(CommandHandler('delchannel', del_channel))
    app.add_handler(CommandHandler('listchannels', list_channels))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    logger.info("✅ Bot is running...")
    await app.run_polling()

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
