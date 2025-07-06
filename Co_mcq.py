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

user_selected_channels = defaultdict(list)
user_broadcast_state = defaultdict(bool)

# === Handlers و باقي الأوامر هنا ===
# (استخدم النسخة التي كتبتها سابقًا أو يمكنني لصقها كاملة أيضاً)

# ✅ الجزء الأخير لحل مشكلة Railway

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
    import nest_asyncio
    import asyncio

    try:
        asyncio.get_event_loop().run_until_complete(main())
    except RuntimeError as e:
        if "already running" in str(e):
            nest_asyncio.apply()
            asyncio.get_event_loop().run_until_complete(main())
        else:
            raise
