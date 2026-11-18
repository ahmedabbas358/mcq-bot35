"""
Utility functions, text parsing helpers, language inference, and formatting.
"""

import logging
import os
import re
from typing import Optional, Tuple, Union

try:
    import psutil
except ImportError:
    psutil = None

try:
    from langdetect import DetectorFactory, LangDetectException, detect
    DetectorFactory.seed = 0
except ImportError:
    detect = None
    class LangDetectException(Exception):
        pass

try:
    from telegram.constants import ChatType
except ImportError:
    class ChatType:
        PRIVATE = "private"
        GROUP = "group"
        SUPERGROUP = "supergroup"
        CHANNEL = "channel"

from bot.config import (
    AI_DEFAULT_COUNT,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    Target,
)
from bot.constants import (
    AI_PROVIDER_ALIASES,
    AI_PROVIDER_CATALOG,
    AI_TOOL_ALIASES,
    AI_TOOL_CATALOG,
    FREE_MODEL_ALIASES,
    FREE_MODEL_CATALOG,
    FUN_STYLE_CHOICES,
)
from bot.i18n.texts import get_text

logger = logging.getLogger(__name__)


def should_delete_source_message(setting_delete: bool, chat_type: Optional[str], chat_id: Optional[int]) -> bool:
    """Check if the source message should be deleted based on settings."""
    if not setting_delete or not chat_type:
        return False
    return chat_type == ChatType.PRIVATE or chat_type in {ChatType.GROUP, ChatType.SUPERGROUP}


def log_memory_usage() -> None:
    """Log current process memory usage."""
    if psutil is None:
        return
    try:
        process = psutil.Process()
        mem_info = process.memory_info()
        logger.info("Memory usage: RSS=%.2f MB, VMS=%.2f MB", mem_info.rss / 1024 / 1024, mem_info.vms / 1024 / 1024)
    except Exception as exc:
        logger.warning("Could not log memory usage: %s", exc)


def has_arabic(text: str) -> bool:
    """Check if the text contains any Arabic characters."""
    return bool(re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]", text or ""))


def infer_lang(preferred: Optional[str] = None, text: str = "") -> str:
    """Infer whether Arabic or English should be used."""
    if preferred in {"ar", "en"}:
        return preferred
    if text:
        if has_arabic(text):
            return "ar"
        if detect is not None:
            try:
                detected = detect(text)
                if detected == "ar":
                    return "ar"
                if detected:
                    return "en"
            except LangDetectException:
                pass
    return "ar"


def extract_message_text(message) -> str:
    """Extract full text from a message or its caption."""
    if not message:
        return ""
    return (message.text or message.caption or "").strip()


def quote_plus(value: str) -> str:
    """URL encode characters safely."""
    safe_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    out = []
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
    """Resolve human-readable title or username for a chat."""
    if not chat:
        return ""
    return (getattr(chat, "title", None) or getattr(chat, "full_name", None) or getattr(chat, "username", None) or str(getattr(chat, "id", ""))).strip()


def serialize_target(target: Optional[Target]) -> Optional[str]:
    """Serialize a target chat into a string for database storage."""
    if target is None:
        return None
    return str(target).strip()


def deserialize_target(raw: Optional[str]) -> Optional[Target]:
    """Deserialize a stored target chat string."""
    if raw is None:
        return None
    cleaned = str(raw).strip()
    if not cleaned:
        return None
    if cleaned.startswith("-") and cleaned[1:].isdigit():
        return int(cleaned)
    if cleaned.isdigit():
        return int(cleaned)
    return cleaned


def format_target_label(target: Optional[Target], title: str = "", lang: str = "ar") -> str:
    """Format a human-readable label for a target chat."""
    if target is None:
        return "الدردشة الحالية" if lang == "ar" else "Current chat"
    if title:
        return f"{title} ({target})"
    return str(target)


def remove_bot_mentions(text: str, bot_username: Optional[str]) -> str:
    """Remove bot username mentions from input text."""
    cleaned = text or ""
    if bot_username:
        cleaned = re.sub(rf"@{re.escape(bot_username)}\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"^\s*@[\w_]+\b", "", cleaned)
    return cleaned.strip()


def parse_target_reference(arg: str, context_chat_id: Optional[int] = None) -> Optional[Target]:
    """Parse user-provided target string into an integer chat ID or @channel."""
    cleaned = (arg or "").strip()
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if lowered in {"here", "here!", "current", "هنا"}:
        return context_chat_id
    if cleaned.startswith("-100") and cleaned[4:].isdigit():
        return int(cleaned)
    if cleaned.startswith("-") and cleaned[1:].isdigit():
        return int(cleaned)
    if cleaned.isdigit():
        return int(cleaned)
    if cleaned.startswith("@"):
        return cleaned
    return None


def parse_ai_count_and_payload(raw_arg: str, default_count: int = AI_DEFAULT_COUNT) -> Tuple[int, str]:
    """Parse count argument prefix and return (count, payload)."""
    text = (raw_arg or "").strip()
    if not text:
        return default_count, ""
    match = re.match(r"^(\d{1,2})\s+(.+)$", text, flags=re.S)
    if match:
        count = max(1, min(10, int(match.group(1))))
        return count, match.group(2).strip()
    return default_count, text


def detect_inline_ai_request(text: str) -> Optional[Tuple[str, str]]:
    """Detect if input text starts with an AI shorthand like '/ai' or 'quizify:'."""
    raw = (text or "").strip()
    if not raw:
        return None
    for pattern, mode in [
        (r"^(?:/ai\b|ai:|\bذكاء\s*اصطناعي\s*:?)\s*(.+)$", "topic"),
        (r"^(?:/quizify\b|quizify:|\bحول\s*إلى\s*أسئلة\s*:?)\s*(.+)$", "quizify"),
        (r"^(?:/study\b|study:|\bذاكر\s*:?|\bمذاكرة\s*:?)\s*(.+)$", "studypack"),
    ]:
        match = re.match(pattern, raw, flags=re.I | re.S)
        if match:
            return mode, match.group(1).strip()
    return None


def humanize_toggle(val: bool, lang: str = "ar") -> str:
    """Format boolean as localized enabled/disabled badge."""
    if lang == "ar":
        return "مفعل ✅" if val else "معطل ❌"
    return "Enabled ✅" if val else "Disabled ❌"


def humanize_share_mode(mode: str, lang: str = "ar") -> str:
    """Format share mode choice."""
    labels = {
        "telegram": {"ar": "تيليجرام فقط", "en": "Telegram only"},
        "web": {"ar": "الويب فقط", "en": "Web only"},
        "both": {"ar": "تيليجرام والويب", "en": "Telegram and web"},
    }
    return labels.get(mode, {}).get(lang if lang == "ar" else "en", mode)


def humanize_language(code: str, lang: str = "ar") -> str:
    """Format language preference choice."""
    labels = {
        "auto": {"ar": "تلقائي", "en": "Auto"},
        "ar": {"ar": "العربية", "en": "Arabic"},
        "en": {"ar": "الإنجليزية", "en": "English"},
    }
    return labels.get(code, {}).get(lang if lang == "ar" else "en", code)


def humanize_delivery_mode(mode: str, lang: str = "ar") -> str:
    """Format delivery mode choice."""
    labels = {
        "rich": {"ar": "كامل (أزرار وتأكيد)", "en": "Rich (buttons + confirm)"},
        "fast": {"ar": "سريع ومباشر", "en": "Fast & direct"},
    }
    return labels.get(mode, {}).get(lang if lang == "ar" else "en", mode)


def normalize_ai_tool(value: str) -> str:
    """Normalize tool keyword using aliases."""
    raw = (value or "").strip().lower()
    return AI_TOOL_ALIASES.get(raw, raw if raw in AI_TOOL_CATALOG else "quiz")


def normalize_ai_provider(value: str) -> str:
    """Normalize provider keyword using aliases."""
    raw = (value or "").strip().lower()
    return AI_PROVIDER_ALIASES.get(raw, raw if raw in AI_PROVIDER_CATALOG else "auto")


def resolve_ai_provider_choice(value: str) -> Optional[str]:
    """Resolve user input to a valid AI provider."""
    normalized = normalize_ai_provider(value)
    return normalized if normalized in AI_PROVIDER_CATALOG else None


def resolve_ai_tool_choice(value: str) -> Optional[str]:
    """Resolve user input to a valid AI tool mode."""
    normalized = normalize_ai_tool(value)
    return normalized if normalized in AI_TOOL_CATALOG else None


def normalize_fun_style(value: str) -> str:
    """Normalize fun break style."""
    raw = (value or "").strip().lower()
    return raw if raw in FUN_STYLE_CHOICES else "mixed"


def humanize_ai_tool(tool: str, lang: str = "ar") -> str:
    """Get human-readable title for an AI tool."""
    pack = AI_TOOL_CATALOG.get(tool, AI_TOOL_CATALOG["quiz"])
    return pack.get("ar" if lang == "ar" else "en", tool)


def humanize_ai_provider(provider: str, lang: str = "ar") -> str:
    """Get human-readable title for an AI provider."""
    pack = AI_PROVIDER_CATALOG.get(provider, AI_PROVIDER_CATALOG["auto"])
    return pack.get("ar" if lang == "ar" else "en", provider)


def normalize_free_model(value: str) -> Optional[str]:
    """Normalize free local model name."""
    raw = (value or "").strip().lower()
    return FREE_MODEL_ALIASES.get(raw, raw if raw in FREE_MODEL_CATALOG else None)


def infer_ai_provider_from_model(model: str) -> str:
    """Infer the best provider from a model name."""
    lowered = (model or "").strip().lower()
    if lowered in FREE_MODEL_CATALOG or lowered in FREE_MODEL_ALIASES or lowered.startswith(("qwen", "gemma", "llama")):
        return "ollama"
    if lowered.startswith("gemini"):
        return "gemini"
    if lowered.startswith("deepseek"):
        return "deepseek"
    if lowered.startswith("mistral"):
        return "mistral"
    if lowered.startswith("grok"):
        return "xai"
    return "auto"


def normalize_ai_model_for_provider(provider: str, model: str) -> str:
    """Adjust default model if empty based on provider."""
    cleaned = (model or "").strip()
    if provider == "ollama":
        if not cleaned or cleaned == OPENAI_MODEL:
            return "qwen2.5:14b-instruct"
        return FREE_MODEL_ALIASES.get(cleaned.lower(), cleaned)
    if provider == "gemini":
        return cleaned or GEMINI_MODEL
    return cleaned or OPENAI_MODEL


def humanize_free_model(model: str, lang: str = "ar") -> str:
    """Get human-readable title for a free model."""
    normalized = normalize_free_model(model) or model
    pack = FREE_MODEL_CATALOG.get(normalized)
    if not pack:
        return model
    return pack.get("ar" if lang == "ar" else "en", model)


def select_free_ai_profile() -> Tuple[str, str]:
    """Choose best available free AI profile (Ollama local or Gemini free tier)."""
    if OPENAI_BASE_URL and ("localhost" in OPENAI_BASE_URL or "127.0.0.1" in OPENAI_BASE_URL):
        return "ollama", "qwen2.5:14b-instruct"
    if GEMINI_API_KEY:
        return "gemini", GEMINI_MODEL
    return "ollama", "qwen2.5:14b-instruct"


def resolve_runtime_model(settings, model_override: Optional[str] = None) -> str:
    """Resolve the active model for current user and settings."""
    if model_override:
        return model_override.strip()
    if settings and getattr(settings, "ai_model", ""):
        return settings.ai_model.strip()
    return OPENAI_MODEL


def humanize_fun_style(style: str, lang: str = "ar") -> str:
    """Get human-readable title for fun break style."""
    labels = {
        "mixed": {"ar": "منوع", "en": "Mixed"},
        "trivia": {"ar": "معلومات عامة", "en": "Trivia"},
        "joke": {"ar": "نكت وفكاهة", "en": "Jokes"},
        "riddle": {"ar": "ألغاز", "en": "Riddles"},
        "icebreaker": {"ar": "كسر الجليد", "en": "Icebreakers"},
        "poll": {"ar": "تصويتات", "en": "Polls"},
        "study": {"ar": "نصائح دراسية", "en": "Study tips"},
    }
    return labels.get(style, {}).get("ar" if lang == "ar" else "en", style)


def humanize_difficulty(level: str, lang: str = "ar") -> str:
    """Format difficulty level with colored indicator."""
    labels = {
        "easy": {"ar": "سهل 🟢", "en": "Easy 🟢"},
        "medium": {"ar": "متوسط 🟡", "en": "Medium 🟡"},
        "hard": {"ar": "متقدم 🔴", "en": "Hard 🔴"},
    }
    return labels.get(level, {}).get("ar" if lang == "ar" else "en", level)


def humanize_fun_interval(interval: int, lang: str = "ar") -> str:
    """Format fun interval with unit."""
    return f"كل {interval} أسئلة" if lang == "ar" else f"Every {interval} quizzes"
