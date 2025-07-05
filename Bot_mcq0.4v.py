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

# Third-party imports
import pytesseract
import pdfplumber
from PIL import Image
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
from transformers import pipeline, AutoModelForSeq2SeqLM, AutoTokenizer
from langdetect import detect, LangDetectException
import aiosqlite
from huggingface_hub import login

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Set Telegram bot token
os.environ['TELEGRAM_BOT_TOKEN'] = '7966858161:AAHxr_eEfjhdsVtLczdPEIUJ7W_4ShoykYo'

# Set Hugging Face token
os.environ['HUGGINGFACE_TOKEN'] = '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'

# Configuration
CONFIG = {
    "DB_FILE": os.getenv("DB_FILE", "sessions.db"),
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
        "file_error": "خطأ أثناء معالجة الملف. تأكد من أن الملف مدعوم (PDF أو صورة: PNG/JPG/JPEG) وحجمه أقل من 5 ميجابايت.",
        "poll_error": "خطأ أثناء إنشاء السؤال: {error}",
        "invalid_premade_format": "تنسيق السؤال غير صالح. استخدم تنسيقًا مثل: س: [السؤال]\\n(a) [خيار1]\\n(b) [خيار2]...\\nالإجابة الصحيحة: [رقم/حرف]"
    },
    "en": {
        "welcome": "Hello! Send a text or file (PDF/image, max 5MB) and I'll generate multiple-choice questions.",
        "invalid_language": "Please use: /language [ar|en]",
        "no_previous_text": "No previous text found. Please send a text or file first.",
        "invalid_args": "Please provide valid numbers: /generate [num_questions (1-10)] [num_options (2-6)]",
        "no_history": "No previous questions found in history.",
        "file_error": "Error processing file. Ensure the file is supported (PDF or image: PNG/JPG/JPEG) and under 5MB.",
        "poll_error": "Error creating poll: {error}",
        "invalid_premade_format": "Invalid question format. Use a format like: Q: [question]\\n(a) [option1]\\n(b) [option2]...\\nAnswer: [number/letter]"
    }
}

async def on_startup(app: Application):
    """تهيئة قاعدة البيانات وتحميل النماذج عند بدء تشغيل البوت."""
    # تسجيل الدخول باستخدام توكن Hugging Face
    login(token=os.getenv('HUGGINGFACE_TOKEN'))
    logger.info("تم تسجيل الدخول إلى Hugging Face بنجاح")

    async with aiosqlite.connect(CONFIG["DB_FILE"]) as db:
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
    
    qa_model = AutoModelForSeq2SeqLM.from_pretrained("iarfmoose/t5-base-question-generator")
    qa_tokenizer = AutoTokenizer.from_pretrained("iarfmoose/t5-base-question-generator")
    app.bot_data["qa_pipeline"] = pipeline("text2text-generation", model=qa_model, tokenizer=qa_tokenizer)
    logger.info("تم تحميل نموذج توليد الأسئلة بنجاح")

    fill_mask_model = "bert-large-uncased" if "en" in CONFIG["SUPPORTED_LANGUAGES"] else "asafaya/bert-base-arabic"
    distractor_model = AutoModelForSeq2SeqLM.from_pretrained(fill_mask_model)
    distractor_tokenizer = AutoTokenizer.from_pretrained(fill_mask_model)
    app.bot_data["distractor_pipeline"] = pipeline("fill-mask", model=distractor_model, tokenizer=distractor_tokenizer)
    logger.info("تم تحميل نموذج الخيارات المشتتة بنجاح")

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

def detect_language(text: str) -> str:
    """كشف لغة النص المدخل."""
    try:
        lang = detect(text)
        return lang if lang in CONFIG["SUPPORTED_LANGUAGES"] else "en"
    except LangDetectException:
        return "en"

def generate_distractors(correct_answer: str, num_distractors: int, context: str, pipeline_func) -> List[str]:
    """توليد خيارات مشتتة ذكية للأسئلة متعددة الخيارات."""
    mask_token = pipeline_func.tokenizer.mask_token
    input_text = f"{context} {mask_token}"
    results = pipeline_func(input_text, top_k=num_distractors + 2)
    distractors = []
    
    for result in results:
        candidate = result['token_str'].strip().capitalize()
        if (candidate != correct_answer and 
            len(distractors) < num_distractors and 
            len(candidate) <= CONFIG["MAX_OPTION_LENGTH"] and
            candidate not in distractors):
            distractors.append(candidate)
    
    while len(distractors) < num_distractors:
        distractor = f"خيار {len(distractors) + 1}"
        if distractor not in distractors:
            distractors.append(distractor)
    
    return distractors[:num_distractors]

async def generate_mcqs(app: Application, text: str, lang: str, num_questions: int, num_options: int) -> List[Tuple[str, List[str], int]]:
    """توليد أسئلة متعددة الخيارات من النص المدخل."""
    qa_pipeline = app.bot_data.get("qa_pipeline")
    distractor_pipeline = app.bot_data.get("distractor_pipeline")
    
    if not qa_pipeline or not distractor_pipeline:
        logger.error("لم يتم تهيئة النماذج")
        return []

    output = await asyncio.to_thread(
        qa_pipeline,
        text,
        max_length=256,
        num_return_sequences=num_questions,
        num_beams=6,
        temperature=0.9
    )
    
    results = []
    correct_text = "✔ الإجابة الصحيحة" if lang == "ar" else "✔ Correct Answer"
    
    for item in output:
        question = item["generated_text"][:CONFIG["MAX_QUESTION_LENGTH"]]
        correct = correct_text
        distractors = generate_distractors(correct, num_options - 1, text, distractor_pipeline)
        opts = [correct] + distractors
        random.shuffle(opts)
        correct_index = opts.index(correct)
        results.append((question, opts, correct_index))
    
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
            return '\n'.join(page.extract_text() or '' for page in pdf.pages)
    elif file_path.lower().endswith(('.png', '.jpg', '.jpeg')):
        image = Image.open(file_path)
        return pytesseract.image_to_string(image, lang='ara+eng')
    else:
        raise ValueError("تنسيق ملف غير مدعوم")

async def send_polls(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, num_q: int, num_opt: int):
    """إرسال استطلاعات الأسئلة متعددة الخيارات إلى محادثة Telegram."""
    user_id = update.effective_user.id
    lang = detect_language(text)  # اكتشاف اللغة مرة واحدة
    
    async with aiosqlite.connect(CONFIG['DB_FILE']) as db:
        await save_session(db, user_id, lang, text)
        messages = MESSAGES.get(lang, MESSAGES['en'])

    premade = parse_premade_mcq(text)
    mcqs = premade or await generate_mcqs(context.application, text, lang, num_q, num_opt)
    
    if not mcqs:
        await update.message.reply_text(messages['poll_error'].format(error="لم يتم توليد أي أسئلة"))
        return

    for q, opts, idx in mcqs:
        try:
            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=q[:CONFIG['MAX_QUESTION_LENGTH']],
                options=[o[:CONFIG['MAX_OPTION_LENGTH']] for o in opts],
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
            await update.message.reply_text(messages['file_error'])
            return
        
        if hasattr(doc, 'file_size') and doc.file_size > CONFIG['MAX_FILE_SIZE']:
            await update.message.reply_text(messages['file_error'])
            return
        
        async with aiohttp.ClientSession() as session:
            f = await doc.get_file()
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, os.path.basename(f.file_path or 'temp_file'))
                await f.download_to_drive(path)
                text = await extract_text_from_media(path)
                await send_polls(update, context, text, CONFIG['DEFAULT_NUM_QUESTIONS'], CONFIG['DEFAULT_NUM_OPTIONS'])

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
        logger.error("لم يتم تعيين متغير بيئة TELEGRAM_BOT_TOKEN")
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