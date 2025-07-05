# Standard library imports
import os
import re
import logging
import tempfile
import random
import json
import asyncio
from typing import Tuple, List, Optional
from datetime import datetime
from pathlib import Path
from functools import wraps

# Third-party imports
import pytesseract
import pdfplumber
from PIL import Image, ImageEnhance
import aiohttp
from telegram import Update, Poll
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.error import TelegramError
from langdetect import detect, LangDetectException
import aiosqlite

# ملاحظة مهمة للبيئة الإنتاجية:
# قبل التشغيل، تأكد من ضبط المتغيرات البيئية التالية:
# - TELEGRAM_BOT_TOKEN: توكن بوت تيليجرام
# - HUGGINGFACE_TOKEN: مفتاح API من Hugging Face
# - ENV: production أو development
# - SQLITE_DB_FILE: (اختياري) مسار قاعدة البيانات، الافتراضي sessions.db

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ضبط مستوى التسجيل بناءً على بيئة التشغيل
if os.getenv('ENV') == 'production':
    logging.getLogger().setLevel(logging.ERROR)
else:
    logging.getLogger().setLevel(logging.INFO)

# Configuration
CONFIG = {
    "DB_FILE": os.getenv("SQLITE_DB_FILE", "sessions.db"),
    "DEFAULT_NUM_QUESTIONS": 5,
    "DEFAULT_NUM_OPTIONS": 4,
    "MAX_QUESTIONS": 10,
    "MAX_OPTIONS": 6,
    "SUPPORTED_LANGUAGES": ["ar", "en"],
    "MAX_QUESTION_LENGTH": 300,
    "MAX_OPTION_LENGTH": 100,
    "MAX_FILE_SIZE": 5 * 1024 * 1024,  # 5MB
}

# Multilingual messages
MESSAGES = {
    "ar": {
        "welcome": "مرحباً! أرسل نصًا أو ملفًا (PDF/صورة، بحد أقصى 5 ميجابايت) وسأولد أسئلة متعددة الخيارات.",
        "invalid_language": "يرجى استخدام: /language [ar|en]",
        "no_previous_text": "لم يتم العثور على نص سابق. أرسل نصًا أو ملفًا أولاً.",
        "invalid_args": "يرجى إدخال أرقام صالحة: /generate [عدد_الأسئلة (1-10)] [عدد_الخيارات (2-6)]",
        "no_history": "لا توجد أسئلة محفوظة في السجل.",
        "file_error": "خطأ أثناء معالجة الملف: {error}",
        "poll_error": "خطأ أثناء إنشاء السؤال: {error}",
        "language_corrected": "تم اكتشاف لغة غير مدعومة. تم تعيين اللغة إلى: {lang}",
        "model_load_error": "فشل الوصول إلى واجهة API بعد عدة محاولات. حاول لاحقًا."
    },
    "en": {
        "welcome": "Hello! Send a text or file (PDF/image, max 5MB) and I'll generate multiple-choice questions.",
        "invalid_language": "Please use: /language [ar|en]",
        "no_previous_text": "No previous text found. Please send a text or file first.",
        "invalid_args": "Please provide valid numbers: /generate [num_questions (1-10)] [num_options (2-6)]",
        "no_history": "No previous questions found in history.",
        "file_error": "Error processing file: {error}",
        "poll_error": "Error creating poll: {error}",
        "language_corrected": "Detected unsupported language. Language set to: {lang}",
        "model_load_error": "Failed to access API after several attempts. Please try again later."
    }
}

def retry_async(attempts: int, delay: int):
    """ديكوريتور لإعادة المحاولة في حالة فشل الاتصال بـ API."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"محاولة {attempt + 1}/{attempts} فشلت: {str(e)}")
                    if attempt < attempts - 1:
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"فشل {func.__name__} بعد {attempts} محاولات")
                        raise
        return wrapper
    return decorator

@retry_async(CONFIG["MODEL_RETRY_ATTEMPTS"], CONFIG["MODEL_RETRY_DELAY"])
async def on_startup(app: Application):
    """تهيئة القيم المطلوبة (فقط نتحقق من التوكن)."""
    HUGGINGFACE_TOKEN = os.getenv('HUGGINGFACE_TOKEN')
    if not HUGGINGFACE_TOKEN:
        logger.error("توكن Hugging Face غير موجود")
        raise ValueError("توكن Hugging Face مطلوب")
    app.bot_data["huggingface_token"] = HUGGINGFACE_TOKEN

    # تهيئة قاعدة البيانات
    db_path = Path(CONFIG["DB_FILE"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                language TEXT,
                last_text TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                question TEXT,
                options TEXT,
                correct_option TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()
    logger.debug("تم تهيئة قاعدة البيانات بنجاح")

async def save_session(db, user_id: int, language: str, text: Optional[str]):
    """حفظ أو تحديث جلسة المستخدم في قاعدة البيانات."""
    await db.execute(
        "REPLACE INTO sessions (user_id, language, last_text, updated_at) VALUES (?, ?, ?, ?)",
        (user_id, language, text, datetime.now())
    )
    await db.commit()
    logger.debug(f"تم حفظ جلسة المستخدم {user_id} بنجاح")

async def get_last_session(db, user_id: int) -> Tuple[Optional[str], Optional[str]]:
    """استرجاع بيانات الجلسة الأخيرة للمستخدم."""
    async with db.execute("SELECT language, last_text FROM sessions WHERE user_id=?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        return (row[0], row[1]) if row else (None, None)

async def save_question(db, user_id: int, question: str, options: List[str], correct_option: str):
    """حفظ سؤال تم إنشاؤه في قاعدة البيانات."""
    await db.execute(
        "INSERT INTO questions (user_id, question, options, correct_option) VALUES (?, ?, ?, ?)",
        (user_id, question, json.dumps(options, ensure_ascii=False), correct_option)
    )
    await db.commit()
    logger.debug(f"تم حفظ سؤال للمستخدم {user_id}: {question}")

async def detect_language(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> str:
    """كشف لغة النص المدخل مع إخطار المستخدم في حالة التصحيح."""
    try:
        lang = detect(text)
        if lang not in CONFIG["SUPPORTED_LANGUAGES"]:
            lang = "en"
            messages = MESSAGES.get(lang, MESSAGES["en"])
            await update.message.reply_text(messages["language_corrected"].format(lang=lang))
        return lang
    except LangDetectException:
        lang = "en"
        messages = MESSAGES.get(lang, MESSAGES["en"])
        await update.message.reply_text(messages["language_corrected"].format(lang=lang))
        return lang

async def query_huggingface_api(model: str, input_text: str, token: str) -> str:
    """إرسال استعلام إلى Hugging Face Inference API."""
    api_url = f"https://api-inference.huggingface.co/models/{model}"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"inputs": input_text}

    async with aiohttp.ClientSession() as session:
        async with session.post(api_url, headers=headers, json=payload, timeout=60) as response:
            if response.status == 200:
                data = await response.json()
                if isinstance(data, list) and "generated_text" in data[0]:
                    return data[0]["generated_text"]
                elif isinstance(data, dict) and "generated_text" in data:
                    return data["generated_text"]
                else:
                    raise ValueError("استجابة غير متوقعة من واجهة API")
            else:
                content = await response.text()
                raise RuntimeError(f"API error {response.status}: {content}")

async def generate_mcqs(app: Application, update: Update, text: str, lang: str, num_questions: int, num_options: int) -> List[Tuple[str, List[str], int]]:
    """توليد أسئلة متعددة الخيارات من النص باستخدام API."""
    token = app.bot_data.get("huggingface_token")
    model = "iarfmoose/t5-base-question-generator"
    results = []

    for _ in range(num_questions):
        try:
            input_text = f"Generate a question and answer based on this text: {text}"
            generated_text = await query_huggingface_api(model, input_text, token)

            question_match = re.match(r"Question: (.+?)\s*Answer: (.+)", generated_text.strip(), re.DOTALL)
            if question_match:
                question = question_match.group(1)[:CONFIG["MAX_QUESTION_LENGTH"]]
                correct_answer = question_match.group(2)[:CONFIG["MAX_OPTION_LENGTH"]]
            else:
                question = generated_text.strip()[:CONFIG["MAX_QUESTION_LENGTH"]]
                correct_answer = "الإجابة الصحيحة" if lang == "ar" else "Correct Answer"

            # استخدام خيارات مشتتة وهمية مؤقتًا
            distractors = [f"{'خيار' if lang == 'ar' else 'Option'} {i+1}" for i in range(num_options - 1)]
            opts = [correct_answer] + distractors
            random.shuffle(opts)
            correct_index = opts.index(correct_answer)
            results.append((question, opts, correct_index))
        except Exception as e:
            logger.error(f"فشل في توليد السؤال عبر API: {str(e)}")
            continue

    if not results:
        messages = MESSAGES.get(lang, MESSAGES["en"])
        await update.message.reply_text(messages["poll_error"].format(error="فشل توليد الأسئلة عبر API"))
    
    logger.debug(f"تم توليد {len(results)} أسئلة من النص")
    return results

PREMADE_PATTERNS = [
    re.compile(
        r"(?:س|Q)[:：]?\s*(?P<q>.+?)\s*"
        r"(?:(?P<o1>.+?)(?:\s*(?P<o2>.+?))?(?:\s*(?P<o3>.+?))?(?:\s*(?P<o4>.+?))?(?:\s*(?P<o5>.+?))?(?:\s*(?P<o6>.+?))?\s*)"
        r"(?:الإجابة\s+الصحيحة|Answer)[:：]?\s*(?P<ans>[١-٦\d]|[a-fA-F])",
        re.S | re.IGNORECASE
    ),
    re.compile(
        r"(?:س|Q)[:：]?\s*(?P<q>.+?)\s*"
        r"(?:(?:[a-fA-F١-٦][\.\)]|\([a-fA-F١-٦]\))\s*(?P<o1>.+?))?"
        r"(?:\s*(?:[a-fA-F١-٦][\.\)]|\([a-fA-F١-٦]\))\s*(?P<o2>.+?))?"
        r"(?:\s*(?:[a-fA-F١-٦][\.\)]|\([a-fA-F١-٦]\))\s*(?P<o3>.+?))?"
        r"(?:\s*(?:[a-fA-F١-٦][\.\)]|\([a-fA-F١-٦]\))\s*(?P<o4>.+?))?"
        r"(?:\s*(?:[a-fA-F١-٦][\.\)]|\([a-fA-F١-٦]\))\s*(?P<o5>.+?))?"
        r"(?:\s*(?:[a-fA-F١-٦][\.\)]|\([a-fA-F١-٦]\))\s*(?P<o6>.+?))?\s*"
        r"(?:الإجابة\s+الصحيحة|Answer)[:：]?\s*(?P<ans>[١-٦\d]|[a-fA-F])",
        re.S | re.IGNORECASE
    ),
]

def parse_premade_mcq(text: str) -> Optional[List[Tuple[str, List[str], int]]]:
    """تحليل أسئلة متعددة الخيارات مسبقة التنسيق من النص."""
    found = []
    for pattern in PREMADE_PATTERNS:
        for m in pattern.finditer(text):
            try:
                q = m.group('q').strip()[:CONFIG["MAX_QUESTION_LENGTH"]]
                opts = [m.group(f'o{i}').strip()[:CONFIG["MAX_OPTION_LENGTH"]] for i in range(1, 7) if m.group(f'o{i}')]
                ans = m.group('ans')
                
                if ans.isdigit() or ans in '١٢٣٤٥٦':
                    ans_idx = int(ans.replace('١', '1').replace('٢', '2').replace('٣', '3')
                                 .replace('٤', '4').replace('٥', '5').replace('٦', '6')) - 1
                else:
                    ans_idx = ord(ans.lower()) - ord('a')
                
                if not 0 <= ans_idx < len(opts):
                    continue
                
                indexed = [{'idx': i, 'text': opts[i]} for i in range(len(opts))]
                random.shuffle(indexed)
                shuffled = [item['text'] for item in indexed]
                new_index = next(i for i, item in enumerate(indexed) if item['idx'] == ans_idx)
                
                found.append((q, shuffled, new_index))
            except (ValueError, IndexError):
                continue
    
    return found if found else None

async def extract_text_from_media(file_path: str) -> str:
    """استخراج النص من ملفات الوسائط (PDF أو صور)."""
    if os.path.getsize(file_path) > CONFIG['MAX_FILE_SIZE']:
        raise ValueError("حجم الملف يتجاوز الحد الأقصى (5 ميجابايت)")
    
    if file_path.lower().endswith('.pdf'):
        with pdfplumber.open(file_path) as pdf:
            text = '\n'.join([page.extract_text() or '' for page in pdf.pages])
            return text
    elif file_path.lower().endswith(('.png', '.jpg', '.jpeg')):
        image = Image.open(file_path)
        # تحسين جودة الصورة لـ OCR
        image = image.convert('L')  # تحويل إلى تدرج رمادي
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)  # زيادة التباين
        return pytesseract.image_to_string(image, lang='ara+eng')
    else:
        raise ValueError("تنسيق ملف غير مدعوم")

async def send_polls(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, num_q: int, num_opt: int):
    """إرسال استطلاعات الأسئلة متعددة الخيارات إلى محادثة Telegram."""
    user_id = update.effective_user.id
    lang = await detect_language(update, context, text)
    async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
        await save_session(db, user_id, lang, text)
        messages = MESSAGES.get(lang, MESSAGES['en'])

    premade = parse_premade_mcq(text)
    mcqs = premade or await generate_mcqs(context.application, update, text, lang, num_q, num_opt)
    
    if not mcqs:
        await update.message.reply_text(messages['poll_error'].format(error="لم يتم توليد أي أسئلة"))
        return

    for q, opts, idx in mcqs:
        try:
            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=q[:CONFIG["MAX_QUESTION_LENGTH"]],
                options=[o[:CONFIG["MAX_OPTION_LENGTH"]] for o in opts],
                type=Poll.QUIZ,
                correct_option_id=idx,
                is_anonymous=False,
                protect_content=True
            )
            async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
                await save_question(db, user_id, q, opts, opts[idx])
        except TelegramError as e:
            await update.message.reply_text(messages['poll_error'].format(error=str(e)))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /start."""
    user_id = update.effective_user.id
    async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
        lang, _ = await get_last_session(db, user_id)
        await update.message.reply_text(MESSAGES.get(lang, MESSAGES['en'])['welcome'])

async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /language لتحديد لغة المستخدم."""
    user_id = update.effective_user.id
    if len(context.args) != 1 or context.args[0] not in CONFIG['SUPPORTED_LANGUAGES']:
        await update.message.reply_text(MESSAGES['en']['invalid_language'])
        return
    
    lang = context.args[0]
    async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
        _, last_text = await get_last_session(db, user_id)
        await save_session(db, user_id, lang, last_text)
        await update.message.reply_text(f"تم تعيين اللغة إلى: {lang}")

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /generate لإنشاء أسئلة من النص المخزن."""
    user_id = update.effective_user.id
    async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
        lang, last = await get_last_session(db, user_id)
        messages = MESSAGES.get(lang, MESSAGES['en'])
        
        if not last:
            await update.message.reply_text(messages['no_previous_text'])
            return
        
        try:
            nq = int(context.args[0]) if context.args else CONFIG['DEFAULT_NUM_QUESTIONS']
            no = int(context.args[1]) if len(context.args) > 1 else CONFIG['DEFAULT_NUM_OPTIONS']
            
            if not (1 <= nq <= CONFIG['MAX_QUESTIONS']) or not (2 <= no <= CONFIG['MAX_OPTIONS']):
                raise ValueError("عدد الأسئلة أو الخيارات غير صالح")
            
            await send_polls(update, context, last, nq, no)
        except ValueError:
            await update.message.reply_text(messages['invalid_args'])

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية لتوليد الأسئلة."""
    text = update.message.text
    await send_polls(update, context, text, CONFIG['DEFAULT_NUM_QUESTIONS'], CONFIG['DEFAULT_NUM_OPTIONS'])

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة ملفات الوسائط (PDF أو صور) لتوليد الأسئلة."""
    user_id = update.effective_user.id
    async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
        lang, _ = await get_last_session(db, user_id)
        messages = MESSAGES.get(lang, MESSAGES['en'])
        
        if update.message.document:
            doc = update.message.document
        elif update.message.photo:
            doc = update.message.photo[-1]
        else:
            await update.message.reply_text(messages['file_error'].format(error="ملف غير مدعوم"))
            return
        
        if hasattr(doc, 'file_size') and doc.file_size > CONFIG['MAX_FILE_SIZE']:
            await update.message.reply_text(messages['file_error'].format(error="حجم الملف كبير جدًا"))
            return
        
        async with aiohttp.ClientSession() as session:
            try:
                file = await context.bot.get_file(doc.file_id)
                with tempfile.TemporaryDirectory() as tmp:
                    path = os.path.join(tmp, os.path.basename(file.file_path or 'temp_file'))
                    await file.download_to_drive(path)
                    text = await extract_text_from_media(path)
                    await send_polls(update, context, text, CONFIG['DEFAULT_NUM_QUESTIONS'], CONFIG['DEFAULT_NUM_OPTIONS'])
            except Exception as e:
                logger.error(f"خطأ في تحميل أو معالجة الملف: {str(e)}")
                await update.message.reply_text(messages['file_error'].format(error=str(e)))
                return

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة أمر /history لعرض الأسئلة الأخيرة."""
    user_id = update.effective_user.id
    async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
        lang, _ = await get_last_session(db, user_id)
        messages = MESSAGES.get(lang, MESSAGES['en'])
        
        async with db.execute(
            "SELECT question, correct_option FROM questions WHERE user_id=? ORDER BY created_at DESC LIMIT 10",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            if not rows:
                await update.message.reply_text(messages['no_history'])
                return
            
            txt = "\n\n".join(f"• {q}\n✔ {a}" for q, a in rows)
            await update.message.reply_text(txt[:4096])

def main():
    """الدالة الرئيسية لتهيئة وتشغيل البوت."""
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    if not TOKEN:
        logger.error("يجب تعيين متغير بيئة TELEGRAM_BOT_TOKEN")
        exit(1)
    
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    
    handlers = [
        CommandHandler('start', start),
        CommandHandler('language', set_language),
        CommandHandler('generate', generate),
        CommandHandler('history', history),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        MessageHandler(filters.Document.ALL | filters.PHOTO, handle_file)
    ]
    
    for handler in handlers:
        app.add_handler(handler)
    
    logger.info("جارٍ تشغيل البوت...")
    app.run_polling()

if __name__ == '__main__':
    main()