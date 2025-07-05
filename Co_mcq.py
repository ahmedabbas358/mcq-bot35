import os
import re
import logging
import random
import asyncio
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
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

# دعم أرقام عربية من 1 إلى 8
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8'}
# دعم حروف عربية من أ إلى ح (8 خيارات)
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3, 'ه': 4, 'و': 5, 'ز': 6, 'ح': 7}

# أنماط الأسئلة، خيارات حتى H وأحرف عربية حتى ح
PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[A-H][).:]\s*.+?\s*){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Ha-h1-8١-٨])",
        re.S | re.IGNORECASE
    ),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[أ-ح][).:]\s*.+?\s*){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-ح1-8١-٨])",
        re.S
    ),
    re.compile(
        r"(?P<q>.+?)\n"
        r"(?P<opts>(?:\s*[A-Za-zء-ي1-8١-٨]+[).:]\s*.+?\n){2,10})"
        r"(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zء-ي1-8١-٨])",
        re.S | re.IGNORECASE
    ),
]

def parse_mcq(text: str):
    for patt in PATTERNS:
        m = patt.search(text)
        if not m:
            continue

        q = m.group('q').strip()
        lines = m.group('opts').strip().splitlines()
        opts = []

        for line in lines:
            parts = re.split(r"^[A-Hأ-حA-Ha-h1-8١-٨][).:]\s*", line.strip(), maxsplit=1)
            if len(parts) == 2:
                opts.append(parts[1].strip())

        raw_ans = m.group('ans').strip()
        ans = ARABIC_DIGITS.get(raw_ans, raw_ans)

        try:
            if ans.isdigit():
                idx = int(ans) - 1
            elif ans.lower() in 'abcdefgh':
                idx = ord(ans.lower()) - ord('a')
            elif raw_ans in AR_LETTERS:
                idx = AR_LETTERS[raw_ans]
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

async def should_respond(update: Update, bot_username: str) -> bool:
    """يرجع True إذا يجب على البوت الرد (رسالة خاصة أو تم ذكره في المجموعة/القناة)"""
    message = update.message
    if not message:
        return False
    if message.chat.type == 'private':
        return True
    # في القروبات والقنوات، يرد فقط إذا ذكر البوت
    if message.entities is not None and message.text:
        for ent in message.entities:
            if ent.type == "mention":
                mention = message.text[ent.offset:ent.offset + ent.length]
                if mention.lower() == f"@{bot_username.lower()}":
                    return True
    return False

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    bot_username = context.bot.username
    if not await should_respond(update, bot_username):
        return  # لا يرد إذا لم يتم ذكره في المجموعة/القناة

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
                await update.message.reply_text("⚠️ حدث خطأ أثناء إرسال السؤال.")

    if not sent:
        buttons = [
            [InlineKeyboardButton("📝 مثال MCQ", callback_data="example")],
            [InlineKeyboardButton("📘 كيف أصيغ السؤال؟", callback_data="help")]
        ]
        await update.message.reply_text(
            "❌ لم أتعرف على أي سؤال. استخدم أحد الصيغ المدعومة.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📝 مثال جاهز", callback_data="example")],
        [InlineKeyboardButton("📘 الصيغ المدعومة", callback_data="help")]
    ]
    await update.message.reply_text(
        f"🤖 أهلاً! أرسل سؤالك بصيغة MCQ لتحويله إلى Quiz.\n"
        f"في المجموعات والقنوات، أذكرني باستخدام @{context.bot.username} لأجيب.",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return
    await update.callback_query.answer()
    cmd = update.callback_query.data
    if cmd == 'help':
        text = (
            "✅ الصيغ المدعومة:\n"
            "Q: ما هو عاصمة فرنسا؟\nA) برلين\nB) باريس\nC) مدريد\nD) روما\nAnswer: B\n\n"
            "س: ما هو عاصمة مصر؟\nأ) الخرطوم\nب) القاهرة\nج) الرياض\nد) تونس\nالإجابة الصحيحة: ب"
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
        "أو بالعربية بنفس البناء."
    )


def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN غير مضبوط في البيئة!")
        return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    logger.info("✅ Bot is running...")
    app.run_polling()


if __name__ == '__main__':
    main()
