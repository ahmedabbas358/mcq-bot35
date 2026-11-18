"""
Configuration settings, environment variables loader, and core data models.
"""

from dataclasses import dataclass
import os
from typing import List, Optional, Union

Target = Union[int, str]


def env_bool(name: str, default: str = "false") -> bool:
    """Read a boolean from environment variables."""
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: str = "0") -> int:
    """Read an integer from environment variables safely."""
    try:
        return int(os.getenv(name, default).strip())
    except Exception:
        return int(default)


def env_float(name: str, default: str = "0.0") -> float:
    """Read a float from environment variables safely."""
    try:
        return float(os.getenv(name, default).strip())
    except Exception:
        return float(default)


def resolve_public_base_url(base_hint: str = "") -> str:
    """Resolve the public domain URL from environment or hints."""
    candidates = [
        os.getenv("PUBLIC_BASE_URL", ""),
        os.getenv("RAILWAY_STATIC_URL", ""),
        os.getenv("RAILWAY_PUBLIC_DOMAIN", ""),
        os.getenv("RENDER_EXTERNAL_URL", ""),
        os.getenv("RENDER_EXTERNAL_HOSTNAME", ""),
        os.getenv("APP_PUBLIC_URL", ""),
        os.getenv("APP_URL", ""),
        os.getenv("VERCEL_URL", ""),
        base_hint,
    ]
    for raw in candidates:
        value = (raw or "").strip().rstrip("/")
        if not value:
            continue
        if "your-app-domain.com" in value.lower():
            continue
        if value.startswith("http://") or value.startswith("https://"):
            return value
        if "." in value or value.startswith("localhost") or value.startswith("127.0.0.1"):
            if value.startswith("localhost") or value.startswith("127.0.0.1"):
                return f"http://{value}"
            return f"https://{value}"
    return ""


# Database & Limits
DB_PATH = os.getenv("DB_PATH", "stats.db")
MAX_QUEUE_SIZE = env_int("MAX_QUEUE_SIZE", "2500")
SEND_INTERVAL = env_float("SEND_INTERVAL", "0.15")
FAST_SEND_INTERVAL = env_float("FAST_SEND_INTERVAL", "0.03")
MAX_CONCURRENT_SEND = env_int("MAX_CONCURRENT_SEND", "8")
MAX_MCQ_BLOCK_LINES = env_int("MAX_MCQ_BLOCK_LINES", "240")
MAX_QUESTION_LENGTH = env_int("MAX_QUESTION_LENGTH", "300")
MAX_OPTION_LENGTH = env_int("MAX_OPTION_LENGTH", "100")
DEFAULT_DELETE_SOURCE = env_bool("DELETE_SOURCE_MESSAGES", "false")

# AI Settings
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip() or "gemini-2.0-flash"
AI_DEFAULT_COUNT = max(1, min(10, env_int("AI_DEFAULT_COUNT", "3")))
AI_MAX_SOURCE_CHARS = env_int("AI_MAX_SOURCE_CHARS", "4000")
QUIZ_CONFIRMATION_MESSAGE = env_bool("QUIZ_CONFIRMATION_MESSAGE", "true")
ENABLE_WEB_PREVIEW = env_bool("ENABLE_WEB_PREVIEW", "true")
AI_OFFLINE_FALLBACK = env_bool("AI_OFFLINE_FALLBACK", "true")
PRESERVE_TARGET_ORDER = env_bool("PRESERVE_TARGET_ORDER", "true")
GROUP_AUTO_PARSE_MCQS = env_bool("GROUP_AUTO_PARSE_MCQS", "true")
AI_BACKEND_FAILURE_COOLDOWN = max(0, env_int("AI_BACKEND_FAILURE_COOLDOWN", "300"))

# Telegram connection settings
CONCURRENT_UPDATES = env_int("CONCURRENT_UPDATES", "64")
GLOBAL_SEND_LIMIT = env_int("GLOBAL_SEND_LIMIT", "100")
LONG_POLL_TIMEOUT = env_int("LONG_POLL_TIMEOUT", "30")

# Public domain for previews
PUBLIC_BASE_URL = resolve_public_base_url()
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
PORT = env_int("PORT", "8080")


@dataclass
class UserSettings:
    default_target: Optional[Target] = None
    default_target_title: str = ""
    delete_source: bool = DEFAULT_DELETE_SOURCE
    ai_enabled: bool = False
    ai_model: str = OPENAI_MODEL
    ai_provider: str = "openai"
    ai_count: int = AI_DEFAULT_COUNT
    preferred_language: str = "auto"
    ai_specialty: str = "general"
    delivery_mode: str = "rich"
    share_mode: str = "telegram"
    show_explanation: bool = True
    confirmation_message: bool = QUIZ_CONFIRMATION_MESSAGE
    ai_tool_mode: str = "default"
    fun_breaks: bool = False
    fun_interval: int = 6
    fun_style: str = "mixed"
    difficulty: str = "medium"


@dataclass
class SendItem:
    target: Target
    question: str
    options: List[str]
    correct_option: int
    quiz_id: str
    owner_user_id: int
    lang: str
    source_chat_id: Optional[int]
    source_message_id: Optional[int]
    source_chat_type: Optional[str]
    delete_source: bool
    explanation: str
