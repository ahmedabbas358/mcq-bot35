import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

import aiosqlite
import psutil
import telegram
from langdetect import DetectorFactory, LangDetectException, detect
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Poll, Update
from telegram.constants import ChatType
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency at runtime
    OpenAI = None

try:
    from keep_alive import keep_alive
except Exception:  # pragma: no cover - optional dependency at runtime
    keep_alive = None


DetectorFactory.seed = 0


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


DB_PATH = os.getenv("DB_PATH", "stats.db")
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "200"))
SEND_INTERVAL = float(os.getenv("SEND_INTERVAL", "0.15"))
FAST_SEND_INTERVAL = float(os.getenv("FAST_SEND_INTERVAL", "0.03"))
MAX_CONCURRENT_SEND = int(os.getenv("MAX_CONCURRENT_SEND", "8"))
MAX_QUESTION_LENGTH = int(os.getenv("MAX_QUESTION_LENGTH", "300"))
MAX_OPTION_LENGTH = int(os.getenv("MAX_OPTION_LENGTH", "100"))
DEFAULT_DELETE_SOURCE = env_bool("DELETE_SOURCE_MESSAGES", "false")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
AI_DEFAULT_COUNT = int(os.getenv("AI_DEFAULT_COUNT", "3"))
AI_MAX_SOURCE_CHARS = int(os.getenv("AI_MAX_SOURCE_CHARS", "4000"))
QUIZ_CONFIRMATION_MESSAGE = env_bool("QUIZ_CONFIRMATION_MESSAGE", "true")
ENABLE_WEB_PREVIEW = env_bool("ENABLE_WEB_PREVIEW", "true")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
CONCURRENT_UPDATES = int(os.getenv("CONCURRENT_UPDATES", "64"))
GLOBAL_SEND_LIMIT = int(os.getenv("GLOBAL_SEND_LIMIT", "100"))
LONG_POLL_TIMEOUT = int(os.getenv("LONG_POLL_TIMEOUT", "30"))


TEXTS = {
    "start": {
        "en": "MCQ Bot is ready. Send formatted MCQs, or use /ai and /quizify for AI-generated quizzes.",
        "ar": "بوت الاختبارات جاهز. أرسل أسئلة بصيغة MCQ، أو استخدم /ai و /quizify لتوليد اختبارات بالذكاء الاصطناعي.",
    },
    "help": {
        "en": (
            "Usage:\n"
            "- Private: send MCQ text directly.\n"
            "- Groups: reply to the bot, mention the bot, or use commands.\n"
            "- Channels: make the bot admin, then post MCQ text or start the post with ai: to generate quizzes.\n\n"
            "Commands:\n"
            "/settings - show current settings\n"
            "/stats - show usage stats\n"
            "/setchannel <chat_id|@channel|here> - set default publishing target\n"
            "/publishhere - set current chat as your default target\n"
            "/clearchannel - clear default target\n"
            "/toggledelete - toggle deleting source messages after publishing\n"
            "/toggleai - enable or disable AI for your account\n"
            "/setmodel <model> - override AI model\n"
            "/setcount <1-10> - default AI quiz count\n"
            "/language <auto|ar|en> - choose the bot language\n"
            "/specialty <text|clear> - set AI specialty or domain\n"
            "/delivery <fast|rich> - prioritize speed or richer buttons\n"
            "/sharemode <telegram|web|both> - choose how share buttons appear\n"
            "/toggleexplain - show or hide explanation button\n"
            "/toggleconfirm - show or hide confirmation message\n"
            "/health - runtime health and queue status\n"
            "/examples - show supported MCQ formats\n"
            "/ai <topic> - generate quizzes from a topic\n"
            "/quizify <text> - convert text into quizzes, or reply to a message with /quizify\n\n"
            "Quick AI shortcuts:\n"
            "- ai: biology chapter 3\n"
            "- quizify: paste lesson text here"
        ),
        "ar": (
            "طريقة الاستخدام:\n"
            "- في الخاص: أرسل نص الأسئلة مباشرة.\n"
            "- في المجموعات: رُد على البوت أو اذكره أو استخدم الأوامر.\n"
            "- في القنوات: اجعل البوت مشرفاً، ثم انشر نص MCQ أو ابدأ المنشور بـ ai: لتوليد الاختبارات.\n\n"
            "الأوامر:\n"
            "/settings - عرض الإعدادات الحالية\n"
            "/stats - عرض الإحصاءات\n"
            "/setchannel <chat_id|@channel|here> - تعيين جهة النشر الافتراضية\n"
            "/publishhere - تعيين الدردشة الحالية كوجهة افتراضية\n"
            "/clearchannel - حذف الوجهة الافتراضية\n"
            "/toggledelete - تبديل حذف رسالة المصدر بعد النشر\n"
            "/toggleai - تشغيل أو إيقاف الذكاء الاصطناعي لحسابك\n"
            "/setmodel <model> - تغيير نموذج الذكاء الاصطناعي\n"
            "/setcount <1-10> - عدد الاختبارات الافتراضي للتوليد\n"
            "/language <auto|ar|en> - اختيار لغة البوت\n"
            "/specialty <text|clear> - ضبط تخصص الذكاء الاصطناعي\n"
            "/delivery <fast|rich> - تفضيل السرعة أو المزايا الغنية\n"
            "/sharemode <telegram|web|both> - اختيار شكل أزرار المشاركة\n"
            "/toggleexplain - إظهار أو إخفاء زر الشرح\n"
            "/toggleconfirm - إظهار أو إخفاء رسالة التأكيد\n"
            "/health - حالة التشغيل والطوابير\n"
            "/examples - عرض صيغ الأسئلة المدعومة\n"
            "/ai <topic> - توليد اختبارات من موضوع\n"
            "/quizify <text> - تحويل نص إلى اختبارات، أو رُد على رسالة باستخدام /quizify\n\n"
            "اختصارات سريعة:\n"
            "- ai: الفصل الثالث أحياء\n"
            "- quizify: الصق النص هنا"
        ),
    },
    "no_q": {
        "en": "No MCQ detected. Send a valid MCQ block or use /ai or /quizify.",
        "ar": "لم أجد سؤال MCQ صالحاً. أرسل صيغة صحيحة أو استخدم /ai أو /quizify.",
    },
    "invalid_format": {
        "en": "Invalid MCQ format or limits exceeded.",
        "ar": "صيغة الأسئلة غير صحيحة أو تجاوزت الحدود المسموح بها.",
    },
    "queue_full": {
        "en": "Queue is full. Try fewer questions or wait a moment.",
        "ar": "قائمة الإرسال ممتلئة حالياً. حاول بعدد أقل أو انتظر قليلاً.",
    },
    "quiz_sent": {"en": "Quiz published.", "ar": "تم نشر الاختبار."},
    "share_quiz": {"en": "Share Quiz", "ar": "مشاركة الاختبار"},
    "repost_quiz": {"en": "Repost", "ar": "إعادة النشر"},
    "show_explanation": {"en": "Explanation", "ar": "الشرح"},
    "stats": {
        "en": (
            "Your activity:\n"
            "- Private/generated by you: {private_count}\n"
            "- Total published across targets: {total_targets}\n"
            "- Current default target: {target}"
        ),
        "ar": (
            "نشاطك:\n"
            "- أسئلة خاصة/منشأة بواسطتك: {private_count}\n"
            "- إجمالي ما تم نشره عبر الوجهات: {total_targets}\n"
            "- الوجهة الافتراضية الحالية: {target}"
        ),
    },
    "settings": {
        "en": (
            "Settings:\n"
            "- Default target: {target}\n"
            "- Delete source after publish: {delete_source}\n"
            "- AI available: {ai_available}\n"
            "- AI enabled for you: {ai_enabled}\n"
            "- AI model: {ai_model}\n"
            "- AI batch size: {ai_count}\n"
            "- Interface language: {language}\n"
            "- AI specialty: {specialty}\n"
            "- Delivery mode: {delivery_mode}\n"
            "- Share mode: {share_mode}\n"
            "- Show explanation button: {show_explanation}\n"
            "- Confirmation message: {confirmation}"
        ),
        "ar": (
            "الإعدادات:\n"
            "- الوجهة الافتراضية: {target}\n"
            "- حذف رسالة المصدر بعد النشر: {delete_source}\n"
            "- توفر خدمة الذكاء الاصطناعي: {ai_available}\n"
            "- الذكاء الاصطناعي مفعل لك: {ai_enabled}\n"
            "- نموذج الذكاء الاصطناعي: {ai_model}\n"
            "- عدد الأسئلة الافتراضي: {ai_count}\n"
            "- لغة الواجهة: {language}\n"
            "- تخصص الذكاء الاصطناعي: {specialty}\n"
            "- وضع الإرسال: {delivery_mode}\n"
            "- وضع المشاركة: {share_mode}\n"
            "- إظهار زر الشرح: {show_explanation}\n"
            "- رسالة التأكيد: {confirmation}"
        ),
    },
    "target_set": {"en": "Default target updated to: {target}", "ar": "تم تحديث الوجهة الافتراضية إلى: {target}"},
    "target_cleared": {
        "en": "Default target cleared. Publishing will use the current chat.",
        "ar": "تم حذف الوجهة الافتراضية. سيستخدم النشر الدردشة الحالية.",
    },
    "delete_enabled": {"en": "Source message deletion is now enabled.", "ar": "تم تفعيل حذف رسالة المصدر بعد النشر."},
    "delete_disabled": {"en": "Source message deletion is now disabled.", "ar": "تم إيقاف حذف رسالة المصدر بعد النشر."},
    "ai_disabled_user": {
        "en": "AI is disabled for your account. Use /toggleai to enable it.",
        "ar": "الذكاء الاصطناعي متوقف لحسابك. استخدم /toggleai لتفعيله.",
    },
    "ai_disabled_global": {
        "en": "AI is not available. Add OPENAI_API_KEY and install dependencies first.",
        "ar": "الذكاء الاصطناعي غير متاح حالياً. أضف OPENAI_API_KEY وثبّت الاعتماديات أولاً.",
    },
    "ai_processing": {"en": "Generating quizzes with AI...", "ar": "جارٍ توليد الاختبارات بالذكاء الاصطناعي..."},
    "ai_done": {
        "en": "AI created {count} quiz item(s) and queued them for publishing.",
        "ar": "أنشأ الذكاء الاصطناعي {count} اختباراً وتمت إضافتها إلى قائمة النشر.",
    },
    "ai_error": {"en": "AI request failed: {reason}", "ar": "فشل طلب الذكاء الاصطناعي: {reason}"},
    "ai_usage_topic": {
        "en": "Usage: /ai <topic> or send a message starting with ai:",
        "ar": "الاستخدام: /ai <topic> أو أرسل رسالة تبدأ بـ ai:",
    },
    "ai_usage_text": {
        "en": "Usage: /quizify <text> or reply to a message with /quizify",
        "ar": "الاستخدام: /quizify <text> أو رُد على رسالة باستخدام /quizify",
    },
    "model_set": {"en": "AI model updated to: {model}", "ar": "تم تحديث نموذج الذكاء الاصطناعي إلى: {model}"},
    "count_set": {"en": "Default AI batch size updated to: {count}", "ar": "تم تحديث العدد الافتراضي لأسئلة الذكاء الاصطناعي إلى: {count}"},
    "language_set": {"en": "Bot language updated to: {language}", "ar": "تم تحديث لغة البوت إلى: {language}"},
    "usage_language": {"en": "Usage: /language <auto|ar|en>", "ar": "الاستخدام: /language <auto|ar|en>"},
    "specialty_set": {"en": "AI specialty updated to: {specialty}", "ar": "تم تحديث تخصص الذكاء الاصطناعي إلى: {specialty}"},
    "usage_specialty": {"en": "Usage: /specialty <text|clear>", "ar": "الاستخدام: /specialty <text|clear>"},
    "delivery_set": {"en": "Delivery mode updated to: {mode}", "ar": "تم تحديث وضع الإرسال إلى: {mode}"},
    "usage_delivery": {"en": "Usage: /delivery <fast|rich>", "ar": "الاستخدام: /delivery <fast|rich>"},
    "sharemode_set": {"en": "Share mode updated to: {mode}", "ar": "تم تحديث وضع المشاركة إلى: {mode}"},
    "usage_sharemode": {
        "en": "Usage: /sharemode <telegram|web|both>",
        "ar": "الاستخدام: /sharemode <telegram|web|both>",
    },
    "toggle_explain_on": {"en": "Explanation button is now enabled.", "ar": "تم تفعيل زر الشرح."},
    "toggle_explain_off": {"en": "Explanation button is now disabled.", "ar": "تم إيقاف زر الشرح."},
    "toggle_confirm_on": {"en": "Confirmation message is now enabled.", "ar": "تم تفعيل رسالة التأكيد."},
    "toggle_confirm_off": {"en": "Confirmation message is now disabled.", "ar": "تم إيقاف رسالة التأكيد."},
    "toggle_ai_on": {"en": "AI is now enabled for your account.", "ar": "تم تفعيل الذكاء الاصطناعي لحسابك."},
    "toggle_ai_off": {"en": "AI is now disabled for your account.", "ar": "تم إيقاف الذكاء الاصطناعي لحسابك."},
    "quiz_loaded": {"en": "Saved quiz loaded into your chat.", "ar": "تم تحميل الاختبار المحفوظ إلى دردشتك."},
    "quiz_missing": {"en": "Quiz not found or no longer available.", "ar": "الاختبار غير موجود أو لم يعد متاحاً."},
    "explanation_missing": {"en": "No explanation is stored for this quiz.", "ar": "لا يوجد شرح محفوظ لهذا الاختبار."},
    "open_preview": {"en": "Preview", "ar": "معاينة"},
    "share_everywhere": {"en": "Share Everywhere", "ar": "مشاركة خارجية"},
    "examples": {
        "en": (
            "Supported formats:\n"
            "1. Standard lines:\nQ: What is 2+2?\nA) 3\nB) 4\nC) 5\nAnswer: B\n\n"
            "2. Arabic labels:\nس: عاصمة مصر؟\nأ) القاهرة\nب) الرياض\nج) دمشق\nالإجابة: أ\n\n"
            "3. Numbered options:\nQuestion: Largest planet?\n1) Mars\n2) Jupiter\n3) Venus\nCorrect Answer: 2\n\n"
            "4. True/False:\nQ: The sun is a star.\nAnswer: True\n\n"
            "5. Bullet options:\nWhat is H2O?\n- Oxygen\n- Water\n- Hydrogen\nAnswer: Water"
        ),
        "ar": (
            "الصيغ المدعومة:\n"
            "1. الصيغة العادية:\nس: كم يساوي 2+2؟\nأ) 3\nب) 4\nج) 5\nالإجابة: ب\n\n"
            "2. بصيغة إنجليزية:\nQ: Largest planet?\nA) Mars\nB) Jupiter\nC) Venus\nAnswer: B\n\n"
            "3. خيارات مرقمة:\nQuestion: Largest planet?\n1) Mars\n2) Jupiter\n3) Venus\nCorrect Answer: 2\n\n"
            "4. صح أو خطأ:\nس: الشمس نجم.\nالإجابة: صح\n\n"
            "5. خيارات بنقاط:\nما هو H2O؟\n- أكسجين\n- ماء\n- هيدروجين\nالإجابة: ماء"
        ),
    },
    "health": {
        "en": (
            "Runtime health:\n"
            "- Memory: {memory_mb} MB\n"
            "- Active target queues: {active_targets}\n"
            "- Pending items: {pending_items}\n"
            "- Concurrent updates: {concurrent_updates}\n"
            "- Per-target send concurrency: {per_target}\n"
            "- Global send limit: {global_limit}"
        ),
        "ar": (
            "حالة التشغيل:\n"
            "- الذاكرة: {memory_mb} MB\n"
            "- عدد وجهات الإرسال النشطة: {active_targets}\n"
            "- العناصر المعلقة: {pending_items}\n"
            "- التحديثات المتزامنة: {concurrent_updates}\n"
            "- التوازي لكل وجهة: {per_target}\n"
            "- الحد العالمي للإرسال: {global_limit}"
        ),
    },
    "unsupported": {"en": "Unsupported action.", "ar": "إجراء غير مدعوم."},
    "usage_setchannel": {"en": "Usage: /setchannel <chat_id|@channel|here>", "ar": "الاستخدام: /setchannel <chat_id|@channel|here>"},
    "usage_setmodel": {"en": "Usage: /setmodel <model-id>", "ar": "الاستخدام: /setmodel <model-id>"},
    "usage_setcount": {"en": "Usage: /setcount <1-10>", "ar": "الاستخدام: /setcount <1-10>"},
    "target_unreachable": {
        "en": "The bot could not access that target. Add the bot there first or check the ID/username.",
        "ar": "لم يتمكن البوت من الوصول إلى هذه الوجهة. أضف البوت هناك أولاً أو تحقق من المعرّف/الاسم.",
    },
    "free_ai_help": {
        "en": "Free local AI is available through Ollama using OPENAI_BASE_URL=http://localhost:11434/v1 and a local model like qwen3:8b.",
        "ar": "يمكنك استخدام ذكاء اصطناعي مجاني محلياً عبر Ollama باستخدام OPENAI_BASE_URL=http://localhost:11434/v1 ونموذج محلي مثل qwen3:8b.",
    },
}


ARABIC_DIGITS = {
    **{str(i): str(i) for i in range(10)},
    **{"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"},
}
ARABIC_LETTERS = {
    "أ": "A",
    "ا": "A",
    "إ": "A",
    "آ": "A",
    "ب": "B",
    "ت": "C",
    "ث": "D",
    "ج": "E",
    "ح": "F",
    "خ": "G",
    "د": "H",
    "ذ": "I",
    "ر": "J",
    "ز": "K",
    "س": "L",
    "ش": "M",
    "ص": "N",
    "ض": "O",
    "ط": "P",
    "ظ": "Q",
    "ع": "R",
    "غ": "S",
    "ف": "T",
    "ق": "U",
    "ك": "V",
    "ل": "W",
    "م": "X",
    "ن": "Y",
    "ه": "Z",
    "ة": "Z",
    "و": "W",
    "ي": "Y",
    "ئ": "Y",
    "ء": "",
}
QUESTION_PREFIXES = ["Q", "Question", "س", "سؤال"]
ANSWER_KEYWORDS = ["Answer", "Ans", "Correct Answer", "الإجابة", "الجواب", "الإجابة الصحيحة"]

Target = Union[int, str]


@dataclass
class UserSettings:
    default_target: Optional[Target]
    default_target_title: str
    delete_source: bool
    ai_enabled: bool
    ai_model: str
    ai_count: int
    preferred_language: str
    ai_specialty: str
    delivery_mode: str
    share_mode: str
    show_explanation: bool
    confirmation_message: bool


@dataclass
class SendItem:
    question: str
    options: List[str]
    correct_index: int
    quiz_id: str
    explanation: str
    owner_user_id: int
    source_chat_id: Optional[int]
    source_message_id: Optional[int]
    delete_source: bool
    lang: str


class DB:
    _conn: Optional[aiosqlite.Connection] = None
    _lock = asyncio.Lock()

    @classmethod
    async def conn(cls) -> aiosqlite.Connection:
        async with cls._lock:
            if cls._conn is None:
                cls._conn = await aiosqlite.connect(DB_PATH)
                cls._conn.row_factory = aiosqlite.Row
                await cls._conn.execute("PRAGMA journal_mode=WAL")
                await cls._conn.execute("PRAGMA synchronous=NORMAL")
            return cls._conn

    @classmethod
    async def close(cls) -> None:
        async with cls._lock:
            if cls._conn is not None:
                await cls._conn.close()
                cls._conn = None


send_queues: Dict[Target, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=MAX_QUEUE_SIZE))
sender_tasks: Dict[Target, List[asyncio.Task]] = defaultdict(list)
_openai_client: Optional["OpenAI"] = None
global_send_semaphore = asyncio.Semaphore(GLOBAL_SEND_LIMIT)


def get_text(key: str, lang: str = "en", **kwargs) -> str:
    lang_key = (lang or "en")[:2]
    text = TEXTS.get(key, {}).get(lang_key) or TEXTS.get(key, {}).get("en", key)
    return text.format(**kwargs)


def log_memory_usage() -> None:
    mem_mb = psutil.Process().memory_info().rss / (1024 * 1024)
    logger.info("Memory usage: %.2f MB", mem_mb)


def has_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def infer_lang(user_lang: Optional[str], sample: str = "") -> str:
    if user_lang:
        code = user_lang[:2].lower()
        if code in {"ar", "en"}:
            return code
    if has_arabic(sample):
        return "ar"
    if sample.strip():
        try:
            detected = detect(sample)
            if detected in {"ar", "en"}:
                return detected
        except LangDetectException:
            pass
    return "en"


def extract_message_text(message: Optional[Message]) -> str:
    if not message:
        return ""
    return (message.text or message.caption or "").strip()


def quote_plus(value: str) -> str:
    safe_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out: List[str] = []
    for char in value or "":
        if char == " ":
            out.append("+")
        elif char in safe_chars:
            out.append(char)
        else:
            for byte in char.encode("utf-8"):
                out.append(f"%{byte:02X}")
    return "".join(out)


def resolve_chat_title(chat) -> str:
    if not chat:
        return ""
    return getattr(chat, "title", "") or getattr(chat, "full_name", "") or getattr(chat, "username", "") or str(chat.id)


def serialize_target(target: Optional[Target]) -> Optional[str]:
    if target is None:
        return None
    return str(target)


def deserialize_target(raw: Optional[str]) -> Optional[Target]:
    if raw is None:
        return None
    raw = raw.strip()
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    return raw


def format_target_label(target: Optional[Target], title: str, lang: str) -> str:
    if target is None:
        return "current chat" if lang == "en" else "الدردشة الحالية"
    if title:
        return f"{title} ({target})"
    return str(target)


def remove_bot_mentions(text: str, bot_username: str) -> str:
    if not text:
        return text
    cleaned = re.sub(rf"@{re.escape(bot_username)}", "", text, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def parse_target_reference(raw: str, current_chat_id: Optional[int] = None) -> Target:
    value = (raw or "").strip()
    if not value:
        raise ValueError("empty target")
    if value.lower() in {"here", "this", "current"}:
        if current_chat_id is None:
            raise ValueError("missing current chat")
        return current_chat_id
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"@[A-Za-z0-9_]{5,}", value):
        return value
    raise ValueError("invalid target")


def parse_ai_count_and_payload(text: str, default_count: int) -> Tuple[int, str]:
    payload = (text or "").strip()
    match = re.match(r"^(\d{1,2})\s+(.+)$", payload, flags=re.S)
    if not match:
        return default_count, payload
    count = max(1, min(10, int(match.group(1))))
    return count, match.group(2).strip()


def detect_inline_ai_request(text: str) -> Optional[Tuple[str, str]]:
    raw = (text or "").strip()
    lower = raw.lower()
    prefixes = [
        ("topic", "ai:"),
        ("topic", "موضوع:"),
        ("text", "quizify:"),
        ("text", "نص:"),
    ]
    for mode, prefix in prefixes:
        if lower.startswith(prefix.lower()):
            return mode, raw[len(prefix):].strip()
    return None


def get_options_blob(options: List[str]) -> str:
    return json.dumps(options, ensure_ascii=False)


def parse_options_blob(blob: str) -> List[str]:
    try:
        data = json.loads(blob)
        if isinstance(data, list):
            return [str(item) for item in data]
    except Exception:
        pass
    return [part for part in (blob or "").split(":::") if part]


def validate_mcq(question: str, options: List[str]) -> bool:
    if not question or not options:
        return False
    if len(question) > MAX_QUESTION_LENGTH:
        return False
    if len(options) < 2 or len(options) > 10:
        return False
    if any(len(opt) > MAX_OPTION_LENGTH for opt in options):
        return False
    return True


def parse_single_mcq(block: str) -> Optional[Tuple[str, List[str], int]]:
    block = re.sub(r"[\u200b\u200c\ufeff]", "", block)
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) > 80:
        return None

    question = None
    options: List[Tuple[str, str]] = []
    answer_label = None
    answer_line = ""
    unlabeled_options: List[str] = []

    question_prefixes = QUESTION_PREFIXES + ["MCQ", "Multiple Choice", "اختبار", "اختر", "أسئلة", "Questions", "السؤال"]
    option_patterns = [
        r"^\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])\s*[).:\-]\s*(.+)",
        r"^\s*[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]\s*(.+)",
        r"^\s*[\u25cb\u25cf\u25a0\u2022\u00d8\*]\s*([a-zأ-ي0-9])\s*[:.]?\s*(.+)",
        r"^\s*([a-zأ-ي0-9])\s*[\u2013\u2014]\s*(.+)",
        r"^\s*\b(?:option|اختيار)\s*([a-zأ-ي0-9])\s*[:.]\s*(.+)",
    ]
    answer_keywords = ANSWER_KEYWORDS + ["Correct", "Solution", "Key", "مفتاح", "صحيح", "صح", "الحل"]
    unlabeled_option_pattern = r"^\s*[-*•]\s+(.+)"

    for line in lines:
        if question is None:
            for prefix in question_prefixes:
                if line.lower().startswith(prefix.lower()):
                    question = re.sub(f"^{re.escape(prefix)}\\s*[:.\\-]?\\s*", "", line, flags=re.I).strip()
                    break
            if question is not None:
                continue

        matched = False
        for pattern in option_patterns:
            match = re.match(pattern, line, re.I | re.U)
            if match:
                label, text = match.groups()
                label = "".join(ARABIC_DIGITS.get(char, char) for char in label).upper()
                label = "".join(ARABIC_LETTERS.get(char, char) for char in label).strip()
                if label:
                    options.append((label, text.strip()))
                    matched = True
                    break
        if matched:
            continue

        unlabeled_match = re.match(unlabeled_option_pattern, line, re.U)
        if unlabeled_match:
            unlabeled_options.append(unlabeled_match.group(1).strip())
            continue

        if answer_label is None:
            for keyword in answer_keywords:
                if keyword.lower() in line.lower():
                    answer_line = line.strip()
                    patterns = [
                        r"[:：]\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])$",
                        r"is\s+([a-zأ-ي0-9])",
                        r"هي\s+([a-zأ-ي0-9])",
                        r"[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]$",
                        r"\b(?:correct|صح|صحيح)\s*[:\-]\s*([a-zأ-ي0-9])",
                        r"[\u2714\u2705]\s*([a-zأ-ي0-9])",
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, line, re.I | re.U)
                        if match:
                            answer_label = match.group(1)
                            answer_label = "".join(ARABIC_DIGITS.get(char, char) for char in answer_label).upper()
                            answer_label = "".join(ARABIC_LETTERS.get(char, char) for char in answer_label).strip()
                            break
                    break

    if not options and 2 <= len(unlabeled_options) <= 10:
        options = [(chr(65 + idx), option) for idx, option in enumerate(unlabeled_options[:10])]

    if question is None and lines:
        question = lines[0]
        if options and lines[0].startswith(options[0][0]):
            question = None

    if question and not options and answer_line:
        lower_answer_line = answer_line.lower()
        if any(token in lower_answer_line for token in ["true", "false", "صح", "خطأ", "صحيح", "غلط"]):
            if has_arabic(question + answer_line):
                options = [("A", "صح"), ("B", "خطأ")]
            else:
                options = [("A", "True"), ("B", "False")]
            if any(token in lower_answer_line for token in ["true", "صح", "صحيح"]):
                answer_label = "A"
            elif any(token in lower_answer_line for token in ["false", "خطأ", "غلط"]):
                answer_label = "B"

    if not question or not options:
        return None

    label_to_idx: Dict[str, int] = {}
    option_text_to_idx: Dict[str, int] = {}
    for idx, (label, option_text) in enumerate(options):
        clean_label = re.sub(r"[^A-Z0-9]", "", label)
        label_to_idx[clean_label] = idx
        if clean_label.isdigit() and 1 <= int(clean_label) <= 26:
            label_to_idx[chr(64 + int(clean_label))] = idx
        option_text_to_idx[re.sub(r"\s+", " ", option_text).strip().lower()] = idx

    if answer_label:
        clean_answer = re.sub(r"[^A-Z0-9]", "", answer_label)
        if clean_answer in label_to_idx:
            return question, [item for _, item in options], label_to_idx[clean_answer]
    else:
        clean_answer = ""

    text_answers = {
        "الأول": "A",
        "أول": "A",
        "أ": "A",
        "1": "A",
        "الثاني": "B",
        "ثاني": "B",
        "ب": "B",
        "2": "B",
        "الثالث": "C",
        "ثالث": "C",
        "ت": "C",
        "3": "C",
        "الرابع": "D",
        "رابع": "D",
        "ث": "D",
        "4": "D",
        "الخامس": "E",
        "خامس": "E",
        "ج": "E",
        "5": "E",
        "first": "A",
        "1st": "A",
        "second": "B",
        "2nd": "B",
        "true": "A",
        "false": "B",
        "صح": "A",
        "خطأ": "B",
        "صحيح": "A",
        "غلط": "B",
    }
    if clean_answer in text_answers and text_answers[clean_answer] in label_to_idx:
        return question, [item for _, item in options], label_to_idx[text_answers[clean_answer]]

    if answer_line:
        normalized_answer_line = re.sub(r"^(?:answer|ans|correct answer|الإجابة|الجواب|الحل|solution)\s*[:\-]?\s*", "", answer_line, flags=re.I).strip().lower()
        normalized_answer_line = re.sub(r"\s+", " ", normalized_answer_line)
        if normalized_answer_line in option_text_to_idx:
            return question, [item for _, item in options], option_text_to_idx[normalized_answer_line]
        for option_text, idx in option_text_to_idx.items():
            if option_text and option_text in normalized_answer_line:
                return question, [item for _, item in options], idx
    return None


def parse_mcq(text: str) -> List[Tuple[str, List[str], int]]:
    text = (text or "").strip()
    if "|" in text:
        text = re.sub(r"\s*\|\s*", "\n", text)
    text = re.sub(r"(?<!\n)(\s+[A-Da-dأ-د1-9][).:\-]\s+)", lambda m: "\n" + m.group(1).strip() + " ", text)
    text = re.sub(r"(?<!\n)(\s+(?:Answer|Ans|Correct Answer|الإجابة|الجواب)\s*[:\-]\s*)", lambda m: "\n" + m.group(1).strip() + " ", text, flags=re.I)

    blocks: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if current and re.match(r"^\s*(?:[Qس]|\d+[.)]|\[)", stripped, re.I):
            blocks.append("\n".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        blocks.append("\n".join(current))

    parsed: List[Tuple[str, List[str], int]] = []
    for block in blocks:
        item = parse_single_mcq(block)
        if item:
            parsed.append(item)
            continue
        sub_blocks = re.split(r"(?=^\s*(?:[Qس]|\d+[.)]|\[))", block, flags=re.M | re.I)
        for sub_block in sub_blocks:
            if sub_block.strip():
                sub_item = parse_single_mcq(sub_block)
                if sub_item:
                    parsed.append(sub_item)
    return parsed


async def ensure_column(conn: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    rows = await (await conn.execute(f"PRAGMA table_info({table})")).fetchall()
    existing = {row["name"] for row in rows}
    if column not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def init_db() -> None:
    conn = await DB.conn()
    await conn.execute("CREATE TABLE IF NOT EXISTS user_stats(user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS channel_stats(chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS known_channels(chat_id INTEGER PRIMARY KEY, title TEXT)")
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS quizzes("
        "quiz_id TEXT PRIMARY KEY, "
        "question TEXT, "
        "options TEXT, "
        "correct_option INTEGER, "
        "user_id INTEGER)"
    )
    await conn.execute("CREATE TABLE IF NOT EXISTS default_channels(user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)")
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS user_settings("
        "user_id INTEGER PRIMARY KEY, "
        "default_target TEXT, "
        "default_target_title TEXT DEFAULT '', "
        "delete_source INTEGER DEFAULT 0, "
        "ai_enabled INTEGER DEFAULT 1, "
        "ai_model TEXT DEFAULT '', "
        "ai_count INTEGER DEFAULT 3, "
        "preferred_language TEXT DEFAULT 'auto', "
        "ai_specialty TEXT DEFAULT '', "
        "delivery_mode TEXT DEFAULT 'rich', "
        "share_mode TEXT DEFAULT 'both', "
        "show_explanation INTEGER DEFAULT 1, "
        "confirmation_message INTEGER DEFAULT 1)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS target_stats("
        "target_id TEXT PRIMARY KEY, "
        "chat_type TEXT DEFAULT '', "
        "title TEXT DEFAULT '', "
        "sent INTEGER DEFAULT 0)"
    )
    await ensure_column(conn, "quizzes", "explanation", "TEXT DEFAULT ''")
    await ensure_column(conn, "quizzes", "created_at", "INTEGER DEFAULT 0")
    await ensure_column(conn, "user_settings", "preferred_language", "TEXT DEFAULT 'auto'")
    await ensure_column(conn, "user_settings", "ai_specialty", "TEXT DEFAULT ''")
    await ensure_column(conn, "user_settings", "delivery_mode", "TEXT DEFAULT 'rich'")
    await ensure_column(conn, "user_settings", "share_mode", "TEXT DEFAULT 'both'")
    await ensure_column(conn, "user_settings", "show_explanation", "INTEGER DEFAULT 1")
    await ensure_column(conn, "user_settings", "confirmation_message", "INTEGER DEFAULT 1")
    await conn.commit()
    logger.info("DB initialized")


async def get_user_settings(user_id: int) -> UserSettings:
    conn = await DB.conn()
    row = await (await conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))).fetchone()
    if row is None:
        legacy = await (await conn.execute("SELECT chat_id, title FROM default_channels WHERE user_id=?", (user_id,))).fetchone()
        default_target = legacy["chat_id"] if legacy else None
        default_title = legacy["title"] if legacy else ""
        await conn.execute(
            "INSERT OR IGNORE INTO user_settings("
            "user_id, default_target, default_target_title, delete_source, ai_enabled, ai_model, ai_count, preferred_language, ai_specialty, delivery_mode, share_mode, show_explanation, confirmation_message"
            ") VALUES (?, ?, ?, ?, 1, ?, ?, 'auto', '', 'rich', 'both', 1, ?)",
            (
                user_id,
                serialize_target(default_target),
                default_title,
                1 if DEFAULT_DELETE_SOURCE else 0,
                OPENAI_MODEL,
                AI_DEFAULT_COUNT,
                1 if QUIZ_CONFIRMATION_MESSAGE else 0,
            ),
        )
        await conn.commit()
        row = await (await conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))).fetchone()

    return UserSettings(
        default_target=deserialize_target(row["default_target"]),
        default_target_title=row["default_target_title"] or "",
        delete_source=bool(row["delete_source"]),
        ai_enabled=bool(row["ai_enabled"]),
        ai_model=(row["ai_model"] or OPENAI_MODEL).strip() or OPENAI_MODEL,
        ai_count=max(1, min(10, int(row["ai_count"] or AI_DEFAULT_COUNT))),
        preferred_language=(row["preferred_language"] or "auto").strip().lower() or "auto",
        ai_specialty=(row["ai_specialty"] or "").strip(),
        delivery_mode=(row["delivery_mode"] or "rich").strip().lower() or "rich",
        share_mode=(row["share_mode"] or "both").strip().lower() or "both",
        show_explanation=bool(row["show_explanation"]),
        confirmation_message=bool(row["confirmation_message"]),
    )


async def update_user_settings(user_id: int, **fields) -> UserSettings:
    current = await get_user_settings(user_id)
    values = {
        "default_target": serialize_target(fields.get("default_target", current.default_target)),
        "default_target_title": fields.get("default_target_title", current.default_target_title),
        "delete_source": 1 if fields.get("delete_source", current.delete_source) else 0,
        "ai_enabled": 1 if fields.get("ai_enabled", current.ai_enabled) else 0,
        "ai_model": fields.get("ai_model", current.ai_model),
        "ai_count": max(1, min(10, int(fields.get("ai_count", current.ai_count)))),
        "preferred_language": (fields.get("preferred_language", current.preferred_language) or "auto").strip().lower(),
        "ai_specialty": (fields.get("ai_specialty", current.ai_specialty) or "").strip(),
        "delivery_mode": (fields.get("delivery_mode", current.delivery_mode) or "rich").strip().lower(),
        "share_mode": (fields.get("share_mode", current.share_mode) or "both").strip().lower(),
        "show_explanation": 1 if fields.get("show_explanation", current.show_explanation) else 0,
        "confirmation_message": 1 if fields.get("confirmation_message", current.confirmation_message) else 0,
    }
    if values["preferred_language"] not in {"auto", "ar", "en"}:
        values["preferred_language"] = "auto"
    if values["delivery_mode"] not in {"fast", "rich"}:
        values["delivery_mode"] = "rich"
    if values["share_mode"] not in {"telegram", "web", "both"}:
        values["share_mode"] = "both"
    conn = await DB.conn()
    await conn.execute(
        "REPLACE INTO user_settings("
        "user_id, default_target, default_target_title, delete_source, ai_enabled, ai_model, ai_count, preferred_language, ai_specialty, delivery_mode, share_mode, show_explanation, confirmation_message"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            values["default_target"],
            values["default_target_title"],
            values["delete_source"],
            values["ai_enabled"],
            values["ai_model"],
            values["ai_count"],
            values["preferred_language"],
            values["ai_specialty"],
            values["delivery_mode"],
            values["share_mode"],
            values["show_explanation"],
            values["confirmation_message"],
        ),
    )
    if values["default_target"] and re.fullmatch(r"-?\d+", values["default_target"]):
        await conn.execute(
            "REPLACE INTO default_channels(user_id, chat_id, title) VALUES (?, ?, ?)",
            (user_id, int(values["default_target"]), values["default_target_title"]),
        )
    else:
        await conn.execute("DELETE FROM default_channels WHERE user_id=?", (user_id,))
    await conn.commit()
    return await get_user_settings(user_id)


async def save_quiz(quiz_id: str, question: str, options: List[str], correct_option: int, user_id: int, explanation: str = "") -> None:
    conn = await DB.conn()
    await conn.execute(
        "INSERT OR IGNORE INTO quizzes(quiz_id, question, options, correct_option, user_id, explanation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (quiz_id, question, get_options_blob(options), correct_option, user_id, explanation, int(time.time())),
    )
    if explanation:
        await conn.execute("UPDATE quizzes SET explanation=? WHERE quiz_id=?", (explanation, quiz_id))
    await conn.commit()


async def fetch_quiz(quiz_id: str) -> Optional[Tuple[str, List[str], int, str, int]]:
    conn = await DB.conn()
    row = await (await conn.execute("SELECT * FROM quizzes WHERE quiz_id=?", (quiz_id,))).fetchone()
    if row is None:
        return None
    return (
        row["question"],
        parse_options_blob(row["options"]),
        int(row["correct_option"]),
        row["explanation"] or "",
        int(row["user_id"] or 0),
    )


async def record_stats(user_id: int, target: Target, chat_type: str, title: str) -> None:
    conn = await DB.conn()
    if user_id:
        await conn.execute("INSERT OR IGNORE INTO user_stats(user_id, sent) VALUES (?, 0)", (user_id,))
        await conn.execute("UPDATE user_stats SET sent=sent+1 WHERE user_id=?", (user_id,))
    target_id = str(target)
    await conn.execute(
        "INSERT OR IGNORE INTO target_stats(target_id, chat_type, title, sent) VALUES (?, ?, ?, 0)",
        (target_id, chat_type or "", title or "", 0),
    )
    await conn.execute(
        "UPDATE target_stats SET sent=sent+1, chat_type=?, title=? WHERE target_id=?",
        (chat_type or "", title or "", target_id),
    )
    if isinstance(target, int) and str(target).startswith("-100"):
        await conn.execute("INSERT OR IGNORE INTO channel_stats(chat_id, sent) VALUES (?, 0)", (target,))
        await conn.execute("UPDATE channel_stats SET sent=sent+1 WHERE chat_id=?", (target,))
        await conn.execute("INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)", (target, title or ""))
    await conn.commit()


def get_openai_client() -> Optional["OpenAI"]:
    global _openai_client
    api_key = AI_API_KEY or ("ollama" if OPENAI_BASE_URL else "")
    if not api_key or OpenAI is None:
        return None
    if _openai_client is None:
        kwargs = {"api_key": api_key}
        if OPENAI_BASE_URL:
            kwargs["base_url"] = OPENAI_BASE_URL
        _openai_client = OpenAI(**kwargs)
    return _openai_client


def ai_service_available() -> bool:
    return get_openai_client() is not None


def normalize_ai_correct_option(value, options_len: int) -> Optional[int]:
    if isinstance(value, int):
        return value if 0 <= value < options_len else None
    if isinstance(value, str):
        raw = value.strip().upper()
        if raw.isdigit():
            idx = int(raw)
            if 0 <= idx < options_len:
                return idx
            if 1 <= idx <= options_len:
                return idx - 1
        if raw and "A" <= raw[:1] <= "Z":
            idx = ord(raw[:1]) - 65
            return idx if 0 <= idx < options_len else None
    return None


def clean_json_text(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def validate_ai_response(payload: dict, expected_count: int) -> List[Tuple[str, List[str], int, str]]:
    quizzes = payload.get("quizzes")
    if not isinstance(quizzes, list) or not quizzes:
        raise ValueError("AI returned no quizzes")

    valid_items: List[Tuple[str, List[str], int, str]] = []
    for item in quizzes[:expected_count]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        options_raw = item.get("options") or []
        if not isinstance(options_raw, list):
            continue
        options = [str(opt).strip() for opt in options_raw if str(opt).strip()]
        correct = normalize_ai_correct_option(item.get("correct_option"), len(options))
        explanation = str(item.get("explanation", "")).strip()
        if validate_mcq(question, options) and correct is not None:
            valid_items.append((question, options, correct, explanation[:700]))

    if not valid_items:
        raise ValueError("AI output did not contain valid quizzes")
    return valid_items


async def generate_quizzes_with_ai(
    mode: str,
    payload: str,
    lang: str,
    count: int,
    model: str,
    specialty: str = "",
) -> List[Tuple[str, List[str], int, str]]:
    client = get_openai_client()
    if client is None:
        raise RuntimeError("AI is unavailable")

    count = max(1, min(10, count))
    payload = payload.strip()[:AI_MAX_SOURCE_CHARS]
    language_name = "Arabic" if lang == "ar" else "English"
    specialty_text = specialty.strip() or ("general education" if lang == "en" else "التعليم العام")
    system_prompt = (
        "You are an assessment designer. Return JSON only. "
        "Create high-quality multiple-choice questions with exactly 4 options each. "
        "Use zero-based integers for correct_option. "
        "Keep questions concise, avoid duplicate options, and keep explanations brief. "
        "Do not invent facts. If the source is insufficient, only ask about explicit content. "
        f"Act like a specialist in this domain: {specialty_text}. "
        "The response JSON must be an object with a single key named quizzes."
    )

    if mode == "topic":
        user_prompt = (
            f"Generate {count} MCQ quizzes in {language_name} from this topic:\n"
            f"{payload}\n\n"
            "Return JSON in this shape:\n"
            '{"quizzes":[{"question":"...","options":["...","...","...","..."],"correct_option":0,"explanation":"..."}]}'
        )
    else:
        user_prompt = (
            f"Read the following source text and create {count} MCQ quizzes in {language_name} "
            "that test understanding of the text.\n\n"
            f"Source text:\n{payload}\n\n"
            "Return JSON in this shape:\n"
            '{"quizzes":[{"question":"...","options":["...","...","...","..."],"correct_option":0,"explanation":"..."}]}'
        )

    def _run():
        kwargs = {
            "model": model,
            "store": False,
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "text": {"format": {"type": "json_object"}},
            "temperature": 0.2,
            "max_output_tokens": 2200,
        }
        if not OPENAI_BASE_URL:
            kwargs["reasoning"] = {"effort": OPENAI_REASONING_EFFORT}
        try:
            return client.responses.create(**kwargs)
        except Exception:
            fallback_kwargs = {
                "model": model,
                "store": False,
                "input": kwargs["input"],
            }
            return client.responses.create(**fallback_kwargs)

    response = await asyncio.to_thread(_run)
    raw_text = clean_json_text(getattr(response, "output_text", "") or "")
    payload_json = json.loads(raw_text)
    return validate_ai_response(payload_json, count)


async def resolve_target_chat(bot, target: Target) -> Optional[Tuple[Target, str]]:
    try:
        chat = await bot.get_chat(target)
        return chat.id, resolve_chat_title(chat)
    except Exception:
        if isinstance(target, int):
            try:
                chat = await bot.get_chat(target)
                return chat.id, resolve_chat_title(chat)
            except Exception:
                return None
        return None


def build_main_keyboard(lang: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("⚙️ Settings" if lang == "en" else "⚙️ الإعدادات", callback_data="settings")],
        [InlineKeyboardButton("📊 Stats" if lang == "en" else "📊 الإحصاءات", callback_data="stats")],
        [InlineKeyboardButton("📘 Help" if lang == "en" else "📘 المساعدة", callback_data="help")],
        [InlineKeyboardButton("🧪 Examples" if lang == "en" else "🧪 أمثلة", callback_data="examples")],
    ]
    return InlineKeyboardMarkup(buttons)


def humanize_toggle(value: bool, lang: str) -> str:
    if lang == "ar":
        return "مفعل" if value else "متوقف"
    return "ON" if value else "OFF"


def humanize_share_mode(mode: str, lang: str) -> str:
    names = {
        "telegram": {"en": "telegram only", "ar": "تيليجرام فقط"},
        "web": {"en": "web preview only", "ar": "معاينة خارجية فقط"},
        "both": {"en": "telegram + web", "ar": "تيليجرام + معاينة خارجية"},
    }
    return names.get(mode, names["both"]).get(lang, names["both"]["en"])


def humanize_language(mode: str, lang: str) -> str:
    names = {
        "auto": {"en": "auto", "ar": "تلقائي"},
        "ar": {"en": "Arabic", "ar": "العربية"},
        "en": {"en": "English", "ar": "الإنجليزية"},
    }
    return names.get(mode, names["auto"]).get(lang, names["auto"]["en"])


def humanize_delivery_mode(mode: str, lang: str) -> str:
    names = {
        "fast": {"en": "fast", "ar": "سريع"},
        "rich": {"en": "rich", "ar": "غني"},
    }
    return names.get(mode, names["rich"]).get(lang, names["rich"]["en"])


async def resolve_user_lang(user_id: int, telegram_lang: Optional[str], sample: str = "") -> str:
    settings = await get_user_settings(user_id)
    if settings.preferred_language in {"ar", "en"}:
        return settings.preferred_language
    return infer_lang(telegram_lang, sample)


def get_preview_url(quiz_id: str) -> str:
    if not ENABLE_WEB_PREVIEW or not PUBLIC_BASE_URL:
        return ""
    return f"{PUBLIC_BASE_URL}/quiz/{quiz_id}"


def build_external_share_rows(preview_url: str, quiz_id: str, question: str, lang: str) -> List[List[InlineKeyboardButton]]:
    if not preview_url:
        return []
    share_text = f"{question[:120]} - {preview_url}"
    whatsapp_url = f"https://wa.me/?text={quote_plus(share_text)}"
    telegram_url = f"https://t.me/share/url?url={quote_plus(preview_url)}&text={quote_plus(question[:120])}"
    x_url = f"https://twitter.com/intent/tweet?url={quote_plus(preview_url)}&text={quote_plus(question[:120])}"
    return [
        [InlineKeyboardButton(get_text("open_preview", lang), url=preview_url)],
        [
            InlineKeyboardButton("Telegram", url=telegram_url),
            InlineKeyboardButton("WhatsApp", url=whatsapp_url),
            InlineKeyboardButton("X", url=x_url),
        ],
    ]


async def build_quiz_keyboard(
    context: ContextTypes.DEFAULT_TYPE,
    quiz_id: str,
    lang: str,
    include_explanation: bool,
    share_mode: str,
    question: str,
) -> InlineKeyboardMarkup:
    bot_username = context.bot_data.get("bot_username")
    if not bot_username:
        me = await context.bot.get_me()
        bot_username = me.username
        context.bot_data["bot_username"] = bot_username

    buttons = []
    if bot_username and share_mode in {"telegram", "both"}:
        share_link = f"https://t.me/{bot_username}?start=quiz_{quiz_id}"
        buttons.append([InlineKeyboardButton(get_text("share_quiz", lang), url=share_link)])

    if share_mode in {"web", "both"}:
        buttons.extend(build_external_share_rows(get_preview_url(quiz_id), quiz_id, question, lang))

    row = [InlineKeyboardButton(get_text("repost_quiz", lang), callback_data=f"repost:{quiz_id}")]
    if include_explanation:
        row.append(InlineKeyboardButton(get_text("show_explanation", lang), callback_data=f"explain:{quiz_id}"))
    buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def send_text_reply(message: Message, text: str, **kwargs) -> None:
    with contextlib.suppress(Exception):
        await message.reply_text(text, **kwargs)


def ensure_sender(target: Target, context: ContextTypes.DEFAULT_TYPE) -> None:
    active_tasks = [task for task in sender_tasks[target] if not task.done()]
    sender_tasks[target] = active_tasks
    missing = max(1, MAX_CONCURRENT_SEND) - len(active_tasks)
    for worker_idx in range(missing):
        task = context.application.create_task(_sender(target, context, worker_idx + len(active_tasks) + 1))
        sender_tasks[target].append(task)


async def _sender(target: Target, context: ContextTypes.DEFAULT_TYPE, worker_idx: int) -> None:
    logger.info("Sender task started for target %s worker %s", target, worker_idx)
    try:
        while True:
            item: SendItem = await send_queues[target].get()
            async with global_send_semaphore:
                try:
                    sent_message = await context.bot.send_poll(
                        chat_id=target,
                        question=item.question,
                        options=item.options,
                        type=Poll.QUIZ,
                        correct_option_id=item.correct_index,
                        is_anonymous=False,
                    )

                    await save_quiz(
                        quiz_id=item.quiz_id,
                        question=item.question,
                        options=item.options,
                        correct_option=item.correct_index,
                        user_id=item.owner_user_id,
                        explanation=item.explanation,
                    )
                    owner_settings = await get_user_settings(item.owner_user_id) if item.owner_user_id else UserSettings(
                        None,
                        "",
                        DEFAULT_DELETE_SOURCE,
                        True,
                        OPENAI_MODEL,
                        AI_DEFAULT_COUNT,
                        "auto",
                        "",
                        "rich",
                        "both",
                        True,
                        QUIZ_CONFIRMATION_MESSAGE,
                    )

                    if item.delete_source and item.source_chat_id and item.source_message_id:
                        with contextlib.suppress(Exception):
                            await context.bot.delete_message(chat_id=item.source_chat_id, message_id=item.source_message_id)

                    await record_stats(
                        user_id=item.owner_user_id,
                        target=target,
                        chat_type=sent_message.chat.type,
                        title=resolve_chat_title(sent_message.chat),
                    )

                    if owner_settings.confirmation_message and owner_settings.delivery_mode != "fast":
                        keyboard = await build_quiz_keyboard(
                            context,
                            quiz_id=item.quiz_id,
                            lang=item.lang,
                            include_explanation=bool(item.explanation) and owner_settings.show_explanation,
                            share_mode=owner_settings.share_mode,
                            question=item.question,
                        )
                        with contextlib.suppress(Exception):
                            await context.bot.send_message(
                                chat_id=target,
                                text=get_text("quiz_sent", item.lang),
                                reply_markup=keyboard,
                            )

                    wait_interval = FAST_SEND_INTERVAL if owner_settings.delivery_mode == "fast" else SEND_INTERVAL
                    if wait_interval > 0:
                        await asyncio.sleep(wait_interval)
                except telegram.error.BadRequest as exc:
                    logger.warning("BadRequest while sending poll to %s: %s", target, exc)
                    await asyncio.sleep(1)
                except Exception as exc:  # pragma: no cover - runtime/network branch
                    logger.exception("Error sending poll to %s: %s", target, exc)
                    await asyncio.sleep(3)
    except asyncio.CancelledError:
        logger.info("Sender task cancelled for %s worker %s", target, worker_idx)
        raise


async def enqueue_quiz_items(
    target: Target,
    quizzes: List[Tuple[str, List[str], int, str]],
    context: ContextTypes.DEFAULT_TYPE,
    owner_user_id: int,
    lang: str,
    source_chat_id: Optional[int] = None,
    source_message_id: Optional[int] = None,
    delete_source: bool = False,
) -> int:
    queued = 0
    for question, options, correct_index, explanation in quizzes:
        if not validate_mcq(question, options):
            continue
        quiz_id = hashlib.md5((question + ":::" + ":::".join(options)).encode()).hexdigest()
        send_queues[target].put_nowait(
            SendItem(
                question=question,
                options=options,
                correct_index=correct_index,
                quiz_id=quiz_id,
                explanation=explanation,
                owner_user_id=owner_user_id,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                delete_source=delete_source,
                lang=lang,
            )
        )
        queued += 1
    if queued:
        ensure_sender(target, context)
    return queued


async def enqueue_mcq(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    explicit_target: Optional[Target] = None,
    owner_user_id: int = 0,
    is_private: bool = False,
    notify_fail: bool = False,
    text_override: Optional[str] = None,
) -> bool:
    raw_message_text = extract_message_text(message)
    try:
        settings = await get_user_settings(owner_user_id) if owner_user_id else UserSettings(
            None, "", DEFAULT_DELETE_SOURCE, True, OPENAI_MODEL, AI_DEFAULT_COUNT, "auto", "", "rich", "both", True, QUIZ_CONFIRMATION_MESSAGE
        )
    except Exception:
        settings = UserSettings(None, "", DEFAULT_DELETE_SOURCE, True, OPENAI_MODEL, AI_DEFAULT_COUNT, "auto", "", "rich", "both", True, QUIZ_CONFIRMATION_MESSAGE)
    lang = settings.preferred_language if settings.preferred_language in {"ar", "en"} else infer_lang(getattr(message.from_user, "language_code", None), raw_message_text)

    target = explicit_target or settings.default_target or message.chat.id
    raw_text = text_override if text_override is not None else raw_message_text

    try:
        results = parse_mcq(raw_text)
    except Exception as exc:
        logger.exception("Parsing failed: %s", exc)
        if notify_fail:
            await send_text_reply(message, get_text("invalid_format", lang))
        return False

    if not results:
        if notify_fail:
            await send_text_reply(message, get_text("no_q", lang))
        return False

    quiz_items = [(question, options, correct_index, "") for question, options, correct_index in results]
    try:
        queued = await enqueue_quiz_items(
            target=target,
            quizzes=quiz_items,
            context=context,
            owner_user_id=owner_user_id,
            lang=lang,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            delete_source=settings.delete_source and is_private,
        )
    except asyncio.QueueFull:
        if notify_fail:
            await send_text_reply(message, get_text("queue_full", lang))
        return False

    if queued == 0:
        if notify_fail:
            await send_text_reply(message, get_text("invalid_format", lang))
        return False
    return True


def message_targets_bot(message: Message, bot_id: int, bot_username: str) -> bool:
    if not message:
        return False
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot_id:
        return True
    text = extract_message_text(message).lower()
    return f"@{bot_username.lower()}" in text


async def show_settings(target_message: Message, user_id: int, lang: str) -> None:
    settings = await get_user_settings(user_id)
    text = get_text(
        "settings",
        lang,
        target=format_target_label(settings.default_target, settings.default_target_title, lang),
        delete_source=humanize_toggle(settings.delete_source, lang),
        ai_available=humanize_toggle(ai_service_available(), lang),
        ai_enabled=humanize_toggle(settings.ai_enabled, lang),
        ai_model=settings.ai_model,
        ai_count=settings.ai_count,
        language=humanize_language(settings.preferred_language, lang),
        specialty=settings.ai_specialty or ("general" if lang == "en" else "عام"),
        delivery_mode=humanize_delivery_mode(settings.delivery_mode, lang),
        share_mode=humanize_share_mode(settings.share_mode, lang),
        show_explanation=humanize_toggle(settings.show_explanation, lang),
        confirmation=humanize_toggle(settings.confirmation_message, lang),
    )
    await send_text_reply(target_message, text, reply_markup=build_main_keyboard(lang))


async def show_stats(target_message: Message, user_id: int, lang: str) -> None:
    settings = await get_user_settings(user_id)
    conn = await DB.conn()
    user_row = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (user_id,))).fetchone()
    total_row = await (await conn.execute("SELECT COALESCE(SUM(sent), 0) AS sent FROM target_stats")).fetchone()
    text = get_text(
        "stats",
        lang,
        private_count=user_row["sent"] if user_row else 0,
        total_targets=total_row["sent"] if total_row else 0,
        target=format_target_label(settings.default_target, settings.default_target_title, lang),
    )
    await send_text_reply(target_message, text, reply_markup=build_main_keyboard(lang))


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    lang = await resolve_user_lang(update.effective_user.id, getattr(update.effective_user, "language_code", None), extract_message_text(message)) if update.effective_user else "en"
    payload = context.args[0] if context.args else ""
    if payload.startswith("quiz_"):
        quiz_id = payload.split("_", 1)[1]
        quiz = await fetch_quiz(quiz_id)
        if quiz is None:
            await send_text_reply(message, get_text("quiz_missing", lang))
            return
        question, options, correct_option, explanation, owner_user_id = quiz
        try:
            await enqueue_quiz_items(
                target=message.chat.id,
                quizzes=[(question, options, correct_option, explanation)],
                context=context,
                owner_user_id=owner_user_id,
                lang=lang,
                source_chat_id=None,
                source_message_id=None,
                delete_source=False,
            )
            await send_text_reply(message, get_text("quiz_loaded", lang))
        except asyncio.QueueFull:
            await send_text_reply(message, get_text("queue_full", lang))
        return
    await send_text_reply(message, get_text("start", lang), reply_markup=build_main_keyboard(lang))


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not update.effective_user:
        return
    lang = await resolve_user_lang(update.effective_user.id, update.effective_user.language_code, extract_message_text(message))
    await send_text_reply(message, get_text("help", lang), reply_markup=build_main_keyboard(lang))


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_user:
        return
    lang = await resolve_user_lang(update.effective_user.id, update.effective_user.language_code, extract_message_text(update.effective_message))
    await show_stats(update.effective_message, update.effective_user.id, lang)


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_user:
        return
    lang = await resolve_user_lang(update.effective_user.id, update.effective_user.language_code, extract_message_text(update.effective_message))
    await show_settings(update.effective_message, update.effective_user.id, lang)


async def setchannel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip()
    if not raw:
        if chat.type != ChatType.PRIVATE:
            target = chat.id
            title = resolve_chat_title(chat)
        else:
            await send_text_reply(message, get_text("usage_setchannel", lang))
            return
    else:
        try:
            requested_target = parse_target_reference(raw, chat.id)
        except ValueError:
            await send_text_reply(message, get_text("usage_setchannel", lang))
            return
        resolved = await resolve_target_chat(context.bot, requested_target)
        if resolved is None:
            await send_text_reply(message, get_text("target_unreachable", lang))
            return
        target, title = resolved

    settings = await update_user_settings(user.id, default_target=target, default_target_title=title)
    await send_text_reply(message, get_text("target_set", lang, target=format_target_label(settings.default_target, settings.default_target_title, lang)))


async def publishhere_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await update_user_settings(user.id, default_target=chat.id, default_target_title=resolve_chat_title(chat))
    await send_text_reply(message, get_text("target_set", lang, target=format_target_label(settings.default_target, settings.default_target_title, lang)))


async def clearchannel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    await update_user_settings(user.id, default_target=None, default_target_title="")
    await send_text_reply(message, get_text("target_cleared", lang))


async def toggledelete_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    updated = await update_user_settings(user.id, delete_source=not settings.delete_source)
    key = "delete_enabled" if updated.delete_source else "delete_disabled"
    await send_text_reply(message, get_text(key, lang))


async def toggleai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    updated = await update_user_settings(user.id, ai_enabled=not settings.ai_enabled)
    key = "toggle_ai_on" if updated.ai_enabled else "toggle_ai_off"
    await send_text_reply(message, get_text(key, lang))


async def setmodel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    model = " ".join(context.args).strip()
    if not model:
        await send_text_reply(message, get_text("usage_setmodel", lang))
        return
    await update_user_settings(user.id, ai_model=model)
    await send_text_reply(message, get_text("model_set", lang, model=model))


async def setcount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    if not context.args or not context.args[0].isdigit():
        await send_text_reply(message, get_text("usage_setcount", lang))
        return
    count = max(1, min(10, int(context.args[0])))
    await update_user_settings(user.id, ai_count=count)
    await send_text_reply(message, get_text("count_set", lang, count=count))


async def language_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    value = " ".join(context.args).strip().lower()
    if value not in {"auto", "ar", "en"}:
        await send_text_reply(message, get_text("usage_language", lang))
        return
    settings = await update_user_settings(user.id, preferred_language=value)
    effective_lang = settings.preferred_language if settings.preferred_language in {"ar", "en"} else infer_lang(user.language_code, "")
    await send_text_reply(message, get_text("language_set", effective_lang, language=humanize_language(settings.preferred_language, effective_lang)))


async def specialty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    value = " ".join(context.args).strip()
    if not value:
        await send_text_reply(message, get_text("usage_specialty", lang))
        return
    if value.lower() in {"clear", "none", "off"}:
        value = ""
    settings = await update_user_settings(user.id, ai_specialty=value[:120])
    await send_text_reply(message, get_text("specialty_set", lang, specialty=settings.ai_specialty or ("general" if lang == "en" else "عام")))


async def delivery_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    value = " ".join(context.args).strip().lower()
    if value not in {"fast", "rich"}:
        await send_text_reply(message, get_text("usage_delivery", lang))
        return
    settings = await update_user_settings(user.id, delivery_mode=value)
    await send_text_reply(message, get_text("delivery_set", lang, mode=humanize_delivery_mode(settings.delivery_mode, lang)))


async def examples_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    text = f"{get_text('examples', lang)}\n\n{get_text('free_ai_help', lang)}"
    await send_text_reply(message, text, reply_markup=build_main_keyboard(lang))


async def sharemode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    mode = " ".join(context.args).strip().lower()
    if mode not in {"telegram", "web", "both"}:
        await send_text_reply(message, get_text("usage_sharemode", lang))
        return
    settings = await update_user_settings(user.id, share_mode=mode)
    await send_text_reply(message, get_text("sharemode_set", lang, mode=humanize_share_mode(settings.share_mode, lang)))


async def toggleexplain_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    updated = await update_user_settings(user.id, show_explanation=not settings.show_explanation)
    key = "toggle_explain_on" if updated.show_explanation else "toggle_explain_off"
    await send_text_reply(message, get_text(key, lang))


async def toggleconfirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    updated = await update_user_settings(user.id, confirmation_message=not settings.confirmation_message)
    key = "toggle_confirm_on" if updated.confirmation_message else "toggle_confirm_off"
    await send_text_reply(message, get_text(key, lang))


async def health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    memory_mb = f"{psutil.Process().memory_info().rss / (1024 * 1024):.2f}"
    pending_items = sum(queue.qsize() for queue in send_queues.values())
    active_targets = len(
        {target for target, queue in send_queues.items() if queue.qsize() > 0}
        | {target for target, tasks in sender_tasks.items() if any(not task.done() for task in tasks)}
    )
    text = get_text(
        "health",
        lang,
        memory_mb=memory_mb,
        active_targets=active_targets,
        pending_items=pending_items,
        concurrent_updates=CONCURRENT_UPDATES,
        per_target=MAX_CONCURRENT_SEND,
        global_limit=GLOBAL_SEND_LIMIT,
    )
    await send_text_reply(message, text, reply_markup=build_main_keyboard(lang))


async def run_ai_flow(
    *,
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    owner_user_id: int,
    lang: str,
    mode: str,
    payload: str,
    explicit_target: Optional[Target] = None,
) -> None:
    settings = await get_user_settings(owner_user_id) if owner_user_id else UserSettings(
        None, "", DEFAULT_DELETE_SOURCE, True, OPENAI_MODEL, AI_DEFAULT_COUNT, "auto", "", "rich", "both", True, QUIZ_CONFIRMATION_MESSAGE
    )
    if not ai_service_available():
        await send_text_reply(message, get_text("ai_disabled_global", lang))
        return
    if owner_user_id and not settings.ai_enabled:
        await send_text_reply(message, get_text("ai_disabled_user", lang))
        return

    count, clean_payload = parse_ai_count_and_payload(payload, settings.ai_count)
    if not clean_payload:
        await send_text_reply(message, get_text("ai_usage_topic" if mode == "topic" else "ai_usage_text", lang))
        return

    status_message = None
    with contextlib.suppress(Exception):
        status_message = await message.reply_text(get_text("ai_processing", lang))

    try:
        quizzes = await generate_quizzes_with_ai(mode, clean_payload, lang, count, settings.ai_model, settings.ai_specialty)
        target = explicit_target or settings.default_target or message.chat.id
        queued = await enqueue_quiz_items(
            target=target,
            quizzes=quizzes,
            context=context,
            owner_user_id=owner_user_id,
            lang=lang,
            source_chat_id=None,
            source_message_id=None,
            delete_source=False,
        )
        if status_message:
            with contextlib.suppress(Exception):
                await status_message.edit_text(get_text("ai_done", lang, count=queued))
        elif queued:
            await send_text_reply(message, get_text("ai_done", lang, count=queued))
    except asyncio.QueueFull:
        if status_message:
            with contextlib.suppress(Exception):
                await status_message.edit_text(get_text("queue_full", lang))
        else:
            await send_text_reply(message, get_text("queue_full", lang))
    except Exception as exc:
        logger.exception("AI flow failed: %s", exc)
        error_text = get_text("ai_error", lang, reason=str(exc)[:180])
        if status_message:
            with contextlib.suppress(Exception):
                await status_message.edit_text(error_text)
        else:
            await send_text_reply(message, error_text)


async def ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    payload = " ".join(context.args).strip()
    await run_ai_flow(message=message, context=context, owner_user_id=user.id, lang=lang, mode="topic", payload=payload)


async def quizify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    payload = " ".join(context.args).strip()
    if not payload and message.reply_to_message:
        payload = extract_message_text(message.reply_to_message)
    await run_ai_flow(message=message, context=context, owner_user_id=user.id, lang=lang, mode="text", payload=payload)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    user = update.effective_user
    lang = await resolve_user_lang(user.id, getattr(user, "language_code", None), "") if user else "en"

    if data == "help":
        with contextlib.suppress(Exception):
            await query.edit_message_text(get_text("help", lang), reply_markup=build_main_keyboard(lang))
        return
    if data == "examples":
        with contextlib.suppress(Exception):
            await query.edit_message_text(f"{get_text('examples', lang)}\n\n{get_text('free_ai_help', lang)}", reply_markup=build_main_keyboard(lang))
        return
    if data == "stats" and user:
        conn = await DB.conn()
        settings = await get_user_settings(user.id)
        user_row = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (user.id,))).fetchone()
        total_row = await (await conn.execute("SELECT COALESCE(SUM(sent), 0) AS sent FROM target_stats")).fetchone()
        text = get_text(
            "stats",
            lang,
            private_count=user_row["sent"] if user_row else 0,
            total_targets=total_row["sent"] if total_row else 0,
            target=format_target_label(settings.default_target, settings.default_target_title, lang),
        )
        with contextlib.suppress(Exception):
            await query.edit_message_text(text, reply_markup=build_main_keyboard(lang))
        return
    if data == "settings" and user:
        settings = await get_user_settings(user.id)
        text = get_text(
            "settings",
            lang,
            target=format_target_label(settings.default_target, settings.default_target_title, lang),
            delete_source=humanize_toggle(settings.delete_source, lang),
            ai_available=humanize_toggle(ai_service_available(), lang),
            ai_enabled=humanize_toggle(settings.ai_enabled, lang),
            ai_model=settings.ai_model,
            ai_count=settings.ai_count,
            language=humanize_language(settings.preferred_language, lang),
            specialty=settings.ai_specialty or ("general" if lang == "en" else "عام"),
            delivery_mode=humanize_delivery_mode(settings.delivery_mode, lang),
            share_mode=humanize_share_mode(settings.share_mode, lang),
            show_explanation=humanize_toggle(settings.show_explanation, lang),
            confirmation=humanize_toggle(settings.confirmation_message, lang),
        )
        with contextlib.suppress(Exception):
            await query.edit_message_text(text, reply_markup=build_main_keyboard(lang))
        return
    if data.startswith("repost:") and query.message:
        quiz_id = data.split(":", 1)[1]
        quiz = await fetch_quiz(quiz_id)
        if quiz is None:
            with contextlib.suppress(Exception):
                await query.answer(get_text("quiz_missing", lang), show_alert=True)
            return
        question, options, correct_option, explanation, owner_user_id = quiz
        try:
            await enqueue_quiz_items(
                target=query.message.chat.id,
                quizzes=[(question, options, correct_option, explanation)],
                context=context,
                owner_user_id=owner_user_id,
                lang=lang,
                source_chat_id=None,
                source_message_id=None,
                delete_source=False,
            )
            with contextlib.suppress(Exception):
                await query.answer(get_text("quiz_loaded", lang), show_alert=False)
        except asyncio.QueueFull:
            with contextlib.suppress(Exception):
                await query.answer(get_text("queue_full", lang), show_alert=True)
        return
    if data.startswith("explain:") and query.message:
        quiz_id = data.split(":", 1)[1]
        quiz = await fetch_quiz(quiz_id)
        if quiz is None:
            with contextlib.suppress(Exception):
                await query.answer(get_text("quiz_missing", lang), show_alert=True)
            return
        explanation = quiz[3]
        if not explanation:
            with contextlib.suppress(Exception):
                await query.answer(get_text("explanation_missing", lang), show_alert=True)
            return
        with contextlib.suppress(Exception):
            await query.message.reply_text(explanation)
        return

    with contextlib.suppress(Exception):
        await query.answer(get_text("unsupported", lang), show_alert=True)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post:
        return
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat:
        return
    raw_text = extract_message_text(message)
    if not raw_text:
        return

    lang = await resolve_user_lang(user.id, getattr(user, "language_code", None), raw_text) if user else infer_lang(None, raw_text)
    inline_request = detect_inline_ai_request(raw_text)
    if chat.type == ChatType.PRIVATE:
        if inline_request and user:
            await run_ai_flow(
                message=message,
                context=context,
                owner_user_id=user.id,
                lang=lang,
                mode=inline_request[0],
                payload=inline_request[1],
            )
            return
        await enqueue_mcq(message, context, owner_user_id=user.id if user else 0, is_private=True, notify_fail=True)
        return

    bot_username = context.bot_data.get("bot_username")
    bot_id = context.bot_data.get("bot_id")
    if not bot_username or not bot_id:
        me = await context.bot.get_me()
        bot_username = me.username
        bot_id = me.id
        context.bot_data["bot_username"] = bot_username
        context.bot_data["bot_id"] = bot_id

    if not message_targets_bot(message, bot_id, bot_username):
        return

    cleaned_text = remove_bot_mentions(raw_text, bot_username)
    inline_request = detect_inline_ai_request(cleaned_text)
    if inline_request and user:
        await run_ai_flow(
            message=message,
            context=context,
            owner_user_id=user.id,
            lang=lang,
            mode=inline_request[0],
            payload=inline_request[1],
            explicit_target=chat.id,
        )
        return

    await enqueue_mcq(
        message,
        context,
        explicit_target=chat.id,
        owner_user_id=user.id if user else 0,
        is_private=False,
        notify_fail=True,
        text_override=cleaned_text,
    )


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    post = update.channel_post
    if not post:
        return
    text = extract_message_text(post)
    if not text:
        return

    lang = infer_lang(None, text)
    conn = await DB.conn()
    await conn.execute(
        "INSERT OR IGNORE INTO known_channels(chat_id, title) VALUES (?, ?)",
        (post.chat.id, resolve_chat_title(post.chat)),
    )
    await conn.commit()

    inline_request = detect_inline_ai_request(text)
    if inline_request:
        await run_ai_flow(
            message=post,
            context=context,
            owner_user_id=0,
            lang=lang,
            mode=inline_request[0],
            payload=inline_request[1],
            explicit_target=post.chat.id,
        )
        return

    await enqueue_mcq(post, context, explicit_target=post.chat.id, owner_user_id=0, is_private=False, notify_fail=False)


async def schedule_cleanup() -> None:
    while True:
        await asyncio.sleep(86400)
        try:
            conn = await DB.conn()
            ninety_days_ago = int(time.time()) - (90 * 24 * 60 * 60)
            await conn.execute("DELETE FROM quizzes WHERE created_at > 0 AND created_at < ?", (ninety_days_ago,))
            await conn.commit()
            log_memory_usage()
            logger.info("Cleanup completed")
        except Exception as exc:
            logger.exception("Cleanup error: %s", exc)


async def post_init(app) -> None:
    await init_db()
    me = await app.bot.get_me()
    app.bot_data["bot_username"] = me.username or ""
    app.bot_data["bot_id"] = me.id
    if ENABLE_WEB_PREVIEW and keep_alive is not None:
        with contextlib.suppress(Exception):
            keep_alive()
    app.create_task(schedule_cleanup())
    logger.info("Bot initialized")


async def on_shutdown(app) -> None:
    logger.info("Shutting down bot...")
    all_tasks = [task for tasks in sender_tasks.values() for task in tasks]
    for task in all_tasks:
        task.cancel()
    if all_tasks:
        await asyncio.gather(*all_tasks, return_exceptions=True)
    await DB.close()
    logger.info("Shutdown complete")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    builder = ApplicationBuilder().token(token).post_init(post_init)
    if hasattr(builder, "concurrent_updates"):
        builder = builder.concurrent_updates(CONCURRENT_UPDATES)
    if hasattr(builder, "connection_pool_size"):
        builder = builder.connection_pool_size(max(GLOBAL_SEND_LIMIT, CONCURRENT_UPDATES * 2))
    if hasattr(builder, "pool_timeout"):
        builder = builder.pool_timeout(60.0)
    if hasattr(builder, "connect_timeout"):
        builder = builder.connect_timeout(30.0)
    if hasattr(builder, "read_timeout"):
        builder = builder.read_timeout(float(LONG_POLL_TIMEOUT + 5))
    if hasattr(builder, "write_timeout"):
        builder = builder.write_timeout(60.0)
    if hasattr(builder, "post_shutdown"):
        builder = builder.post_shutdown(on_shutdown)
    app = builder.build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("settings", settings_handler))
    app.add_handler(CommandHandler("setchannel", setchannel_handler))
    app.add_handler(CommandHandler("publishhere", publishhere_handler))
    app.add_handler(CommandHandler("clearchannel", clearchannel_handler))
    app.add_handler(CommandHandler("toggledelete", toggledelete_handler))
    app.add_handler(CommandHandler("toggleai", toggleai_handler))
    app.add_handler(CommandHandler("setmodel", setmodel_handler))
    app.add_handler(CommandHandler("setcount", setcount_handler))
    app.add_handler(CommandHandler("language", language_handler))
    app.add_handler(CommandHandler("specialty", specialty_handler))
    app.add_handler(CommandHandler("delivery", delivery_handler))
    app.add_handler(CommandHandler("sharemode", sharemode_handler))
    app.add_handler(CommandHandler("toggleexplain", toggleexplain_handler))
    app.add_handler(CommandHandler("toggleconfirm", toggleconfirm_handler))
    app.add_handler(CommandHandler("health", health_handler))
    app.add_handler(CommandHandler("examples", examples_handler))
    app.add_handler(CommandHandler("ai", ai_handler))
    app.add_handler(CommandHandler("quizify", quizify_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL & (filters.TEXT | filters.Caption), handle_channel_post))
    app.add_handler(MessageHandler((filters.TEXT | filters.Caption) & ~filters.COMMAND, handle_text))

    logger.info("Bot is starting...")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
        close_loop=False,
        timeout=LONG_POLL_TIMEOUT,
    )


if __name__ == "__main__":
    main()
