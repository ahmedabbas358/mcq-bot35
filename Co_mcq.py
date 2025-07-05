# file: co_mcq_bot.py

import os
import re
import logging
import random
import asyncio
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# إعداد سجل الأحداث
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تحويل الأرقام العربية إلى لاتينية
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'}
AR_LETTERS = {
    'أ': 0, 'إ': 0, 'ا': 0,
    'ب': 1,
    'ج': 2,
    'د': 3,
    'هـ': 4, 'ه': 4, 'ه‍': 4,
    'و': 5,
    'ز': 6,
    'ح': 7,
    'ط': 8,
    'ي': 9, 'ى': 9
}

PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[A-J][).:\-–—]?\s*.+?\s*){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ja-j1-9١-٩])",
        re.S | re.IGNORECASE
    ),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[أ-ي][).:\-–—]?\s*.+?\s*){2,10})"
        r"(?:الإجابة\s+الصحيحة|الإجابة)[:：]?\s*(?P<ans>[أ-ي1-9١-٩])",
        re.S
    ),
    re.compile(
        r"(?P<q>.+?)\n"
        r"(?P<opts>(?:\s*[\(\[]?[A-Za-zأ-ي0-9][\)\].:\-–—]?\s*.+?\n){2,10})"
        r"(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zأ-ي0-9١-٩])",
        re.S | re.IGNORECASE
    ),
]

def parse_mcq(text: str):
    results = []
    for patt in PATTERNS:
        for m in patt.finditer(text):
            q = m.group('q').strip()
            lines = m.group('opts').strip().splitlines()
            opts = []

            for line in lines:
                parts = re.split(r"^\s*[\(\[]?[A-Za-zأ-ي٠-٩0-9][\)\].:\-–—]?\s*", line.strip(), maxsplit=1)
                if len(parts) == 2:
                    opts.append(parts[1].strip())

            raw_ans = m.group('ans').strip()
            ans = ARABIC_DIGITS.get(raw_ans, raw_ans)

            try:
                if ans.isdigit():
                    idx = int(ans) - 1
                elif ans.lower() in 'abcdefghij':
                    idx = ord(ans.lower()) - ord('a')
                elif raw_ans in AR_LETTERS:
                    idx = AR_LETTERS[raw_ans]
                else:
                    continue
            except Exception:
                continue

            if len(opts) < 2 or len(opts) > 10:
                continue

            if 0 <= idx < len(opts):
                pairs = list(enumerate(opts))
                random.shuffle(pairs)
                shuffled = [opt for _, opt in pairs]
                new_idx = next(i for i, (orig, _) in enumerate(pairs) if orig == idx)
                results.append((q, shuffled, new_idx))
    return results

async def handle_mcq_message(message: Message, context: ContextTypes.DEFAULT_TYPE):
    text = message.text
    blocks = [blk.strip() for blk in re.split(r"\n{2,}", text) if blk.strip()]
    sent = False

    for blk in blocks:
        mcqs = parse_mcq(blk)
        if not mcqs:
            continue

        sent = True
        for question, opts, correct in mcqs:
            try:
                await context.bot.send_poll(
                    chat_id=message.chat.id,
                    question=question,
                    options=opts,
                    type=Poll.QUIZ,
                    correct_option_id=correct,
                    is_anonymous=False,
                    protect_content=True,
                )
                if message.chat.type != "channel":
                    kb = [[InlineKeyboardButton("👈 سؤال جديد", callback_data="new")]]
                    await context.bot.send_message(
                        chat_id=message.chat.id,
                        text="هل تريد إرسال سؤال آخر؟",
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Error sending poll: {e}")
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text="⚠️ حدث خطأ أثناء إرسال السؤال."
                )

    if sent and message.chat.type != "channel":
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"⚠️ فشل في حذف الرسالة: {e}")

    if not sent:
        buttons = [
            [InlineKeyboardButton("📝 مثال MCQ", callback_data="example")],
            [InlineKeyboardButton("📘 كيف أصيغ السؤال؟", callback_data="help")]
        ]
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="❌ لم أتعرف على أي سؤال. استخدم أحد الصيغ المدعومة.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.effective_chat.type != "channel":
        await handle_mcq_message(update.message, context)

async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.channel_post:
        await handle_mcq_message(update.channel_post, context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📝 مثال جاهز", callback_data="example")],
        [InlineKeyboardButton("📘 الصيغ المدعومة", callback_data="help")]
    ]
    await update.message.reply_text(
        "🤖 أهلاً! أرسل سؤالك بصيغة MCQ لتحويله إلى Quiz.",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cmd = update.callback_query.data
    if cmd == 'help':
        text = (
            "✅ الصيغ المدعومة:\n"
            "Q: ما هو عاصمة فرنسا؟\nA) برلين\nB) باريس\nC) مدريد\nD) روما\nAnswer: B\n\n"
            "س: ما هو عاصمة مصر؟\nأ) الخرطوم\nب) القاهرة\nج) الرياض\nد) تونس\nالإجابة: ب"
        )
    elif cmd == 'example':
        text = (
            "📝 مثال MCQ:\n"
            "Q: What is 2+2?\nA) 3\nB) 4\nC) 5\nD) 6\nAnswer: B"
        )
    elif cmd == 'new':
        text = "📩 أرسل الآن سؤالًا جديدًا بصيغة MCQ!"
    else:
        text = "⚠️ الأمر غير مدعوم."
    await update.callback_query.edit_message_text(text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🆘 استخدم أحد الصيغ التالية:\n"
        "Q: السؤال؟\nA) خيار1\nB) خيار2\nC) خيار3\nD) خيار4\nAnswer: B\n\n"
        "أو بالعربية:\nس: السؤال؟\nأ) ...\nب) ...\nالإجابة: ب"
    )

def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN غير مضبوط في البيئة!")
        return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & filters.TEXT, handle_channel_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("✅ Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
