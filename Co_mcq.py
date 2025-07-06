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

# إعداد سجل الأخطاء
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ===== أنماط الأسئلة المدعومة =====
ARABIC_DIGITS = {'١': '1', '٢': '2', '٣': '3', '٤': '4'}
AR_LETTERS = {'أ': 0, 'ب': 1, 'ج': 2, 'د': 3}

PATTERNS = [
    re.compile(
        r"Q[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[A-D][).:]\s*.+?\s*){2,10})"
        r"(?:Answer|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Da-d1-4١-٤])",
        re.S | re.IGNORECASE
    ),
    re.compile(
        r"س[.:)]?\s*(?P<q>.+?)\s*"
        r"(?P<opts>(?:[أ-د][).:]\s*.+?\s*){2,10})"
        r"الإجابة\s+الصحيحة[:：]?\s*(?P<ans>[أ-د1-4١-٤])",
        re.S
    ),
    re.compile(
        r"(?P<q>.+?)\n"
        r"(?P<opts>(?:\s*[A-Za-zأ-ي0-9]+[).:]\s*.+?\n){2,10})"
        r"(?:Answer|الإجابة|Ans|Correct Answer)[:：]?\s*(?P<ans>[A-Za-zأ-ي0-9١-٤])",
        re.S | re.IGNORECASE
    ),
]

# ===== تحليل السؤال =====
def parse_mcq(text: str):
    for patt in PATTERNS:
        m = patt.search(text)
        if not m:
            continue
        q = m.group('q').strip()
        lines = m.group('opts').strip().splitlines()
        opts = []

        for line in lines:
            parts = re.split(r"^[A-Za-zأ-ي0-9][).:]\s*", line.strip(), maxsplit=1)
            if len(parts) == 2:
                opts.append(parts[1].strip())

        raw_ans = m.group('ans').strip()
        ans = ARABIC_DIGITS.get(raw_ans, raw_ans)

        try:
            if ans.isdigit():
                idx = int(ans) - 1
            elif ans.lower() in 'abcd':
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

# ===== النصوص المستخدمة =====
TEXTS = {
    'no_q': "❌ لم أتعرف على أي سؤال. استخدم إحدى الصيغ المدعومة.",
    'error_poll': "⚠️ حدث خطأ أثناء إرسال السؤال.",
    'example': (
        "📝 مثال MCQ:\n"
        "Q: What is 2+2?\n"
        "A) 3\nB) 4\nC) 5\nD) 6\nAnswer: B"
    ),
    'help': (
        "✅ الصيغ المدعومة:\n\n"
        "Q: ما هو عاصمة فرنسا؟\n"
        "A) برلين\nB) باريس\nC) مدريد\nD) روما\nAnswer: B\n\n"
        "أو بالعربية:\n"
        "س: ما هي عاصمة مصر؟\n"
        "أ) الخرطوم\nب) القاهرة\nج) الرياض\nد) تونس\n"
        "الإجابة الصحيحة: ب"
    ),
    'start': "🤖 أهلاً بك! أرسل سؤالاً بصيغة MCQ وسأقوم بتحويله إلى اختبار."
}

# ===== المعالجة الرئيسية =====
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.channel_post
    text = msg.text or msg.caption
    if not text:
        return

    blocks = [blk.strip() for blk in re.split(r"\n{2,}", text) if blk.strip()]
    sent = False

    for blk in blocks:
        mcqs = parse_mcq(blk)
        if not mcqs:
            continue

        sent = True
        for question, opts, correct in mcqs:
            if not 2 <= len(opts) <= 10:
                await msg.reply_text("❌ عدد الخيارات يجب أن يكون من 2 إلى 10.")
                continue
            try:
                await context.bot.send_poll(
                    chat_id=msg.chat.id,
                    question=question,
                    options=opts,
                    type=Poll.QUIZ,
                    correct_option_id=correct,
                    is_anonymous=False,
                    protect_content=True
                )
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.error(f"Error sending poll: {e}")
                await msg.reply_text(TEXTS['error_poll'])

    if not sent:
        kb = [
            [InlineKeyboardButton("📘 مثال", callback_data="example")],
            [InlineKeyboardButton("📘 المساعدة", callback_data="help")]
        ]
        await msg.reply_text(TEXTS['no_q'], reply_markup=InlineKeyboardMarkup(kb))

# ===== الأوامر =====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📝 مثال", callback_data="example")],
        [InlineKeyboardButton("📘 تعليمات", callback_data="help")]
    ]
    await update.message.reply_text(TEXTS['start'], reply_markup=InlineKeyboardMarkup(kb))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(TEXTS['help'])

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    cmd = update.callback_query.data
    await update.callback_query.edit_message_text(TEXTS.get(cmd, "⚠️ الأمر غير معروف."))

# ===== التشغيل =====
async def main():
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("❌ لم يتم تعيين TELEGRAM_BOT_TOKEN في البيئة!")
        return

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ALL & filters.ChatType.CHANNEL, handle_text))

    logger.info("✅ البوت يعمل الآن...")
    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
 
