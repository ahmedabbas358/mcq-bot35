"""
AI Service supporting OpenAI, Gemini, Ollama, OpenRouter, and custom compatible backends,
with auto-fallback to offline heuristic engine on network or token issues.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple, Union

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

from bot.config import (
    AI_API_KEY,
    AI_BACKEND_FAILURE_COOLDOWN,
    AI_MAX_SOURCE_CHARS,
    AI_OFFLINE_FALLBACK,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    UserSettings,
)
from bot.constants import (
    AI_PROVIDER_CATALOG,
    AI_TOOL_SPECS,
)
from bot.parsers.mcq_parser import validate_mcq
from bot.services.offline_ai import (
    local_ai_tool_text,
    local_quiz_pack,
)
from bot.utils.helpers import (
    normalize_ai_provider,
    normalize_ai_tool,
    resolve_runtime_model,
)

logger = logging.getLogger(__name__)

_openai_clients: Dict[Tuple[str, str], "OpenAI"] = {}
_ai_backend_failure_cache: Dict[Tuple[str, str, str, str], float] = {}


def resolve_ai_runtime(
    settings: Optional[UserSettings] = None,
    model_override: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], str]:
    """Resolve API key, base URL, and model name for the active user settings."""
    provider = normalize_runtime_provider(settings)
    model = (model_override or (settings.ai_model if settings else "") or OPENAI_MODEL).strip() or OPENAI_MODEL

    if provider == "ollama":
        return "ollama", "http://localhost:11434/v1", (model_override or model or "qwen2.5:14b-instruct").strip() or "qwen2.5:14b-instruct"

    if provider == "gemini":
        return GEMINI_API_KEY or None, "https://generativelanguage.googleapis.com/v1beta/openai/", (model_override or GEMINI_MODEL).strip() or GEMINI_MODEL

    preset = AI_PROVIDER_CATALOG.get(provider, AI_PROVIDER_CATALOG["auto"])
    if preset.get("mode") != "compatible" and provider not in {"auto", "custom"}:
        return None, None, model

    base_url = (preset.get("base_url") or OPENAI_BASE_URL or "").strip()
    api_key = ""
    key_env = preset.get("api_key_env") or ""
    if key_env:
        api_key = os.getenv(key_env, "").strip()
    if not api_key:
        api_key = AI_API_KEY or os.getenv("OPENAI_API_KEY", "").strip()

    if provider == "auto":
        if not base_url and OPENAI_BASE_URL:
            base_url = OPENAI_BASE_URL
        if not api_key and AI_API_KEY:
            api_key = AI_API_KEY

    if provider == "custom":
        base_url = OPENAI_BASE_URL
        api_key = AI_API_KEY or os.getenv("OPENAI_API_KEY", "").strip()

    if provider == "auto" and not base_url and not api_key and OPENAI_BASE_URL:
        base_url = OPENAI_BASE_URL

    if not api_key and base_url and (base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1")):
        api_key = "ollama"

    if provider == "ollama" and not model_override and (not settings or settings.ai_model == OPENAI_MODEL):
        model = "qwen2.5:14b-instruct"

    if provider == "auto" and settings and settings.ai_model:
        model = settings.ai_model

    return api_key or None, base_url or None, model


def _ai_backend_signature(settings: Optional[UserSettings] = None, model_override: Optional[str] = None) -> Tuple[str, str, str, str]:
    provider = normalize_runtime_provider(settings)
    api_key, base_url, model = resolve_ai_runtime(settings, model_override)
    return provider, api_key or "", base_url or "", model or ""


def _prune_ai_backend_failures() -> None:
    if not _ai_backend_failure_cache:
        return
    now = time.time()
    for key, expires_at in list(_ai_backend_failure_cache.items()):
        if expires_at <= now:
            _ai_backend_failure_cache.pop(key, None)


def ai_backend_temporarily_disabled(settings: Optional[UserSettings] = None, model_override: Optional[str] = None) -> bool:
    if AI_BACKEND_FAILURE_COOLDOWN <= 0:
        return False
    _prune_ai_backend_failures()
    return _ai_backend_failure_cache.get(_ai_backend_signature(settings, model_override), 0.0) > time.time()


def mark_ai_backend_failed(settings: Optional[UserSettings] = None, model_override: Optional[str] = None) -> None:
    if AI_BACKEND_FAILURE_COOLDOWN <= 0:
        return
    _ai_backend_failure_cache[_ai_backend_signature(settings, model_override)] = time.time() + AI_BACKEND_FAILURE_COOLDOWN


def clear_ai_backend_failure(settings: Optional[UserSettings] = None, model_override: Optional[str] = None) -> None:
    _ai_backend_failure_cache.pop(_ai_backend_signature(settings, model_override), None)


def get_openai_client(settings: Optional[UserSettings] = None) -> Optional["OpenAI"]:
    if ai_backend_temporarily_disabled(settings):
        return None
    api_key, base_url, _ = resolve_ai_runtime(settings)
    if not api_key or OpenAI is None:
        return None
    cache_key = (api_key, base_url or "")
    if cache_key not in _openai_clients:
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _openai_clients[cache_key] = OpenAI(**kwargs)
    return _openai_clients[cache_key]


def ai_service_available(settings: Optional[UserSettings] = None) -> bool:
    return get_openai_client(settings) is not None


def normalize_runtime_provider(settings: Optional[UserSettings] = None) -> str:
    provider = normalize_ai_provider(settings.ai_provider if settings else "auto")
    if provider != "auto":
        return provider
    has_openai_like_runtime = bool(OPENAI_BASE_URL or AI_API_KEY or os.getenv("OPENAI_API_KEY", "").strip())
    if not has_openai_like_runtime and GEMINI_API_KEY:
        return "gemini"
    return provider


def is_gemini_runtime(settings: Optional[UserSettings] = None) -> bool:
    return normalize_runtime_provider(settings) == "gemini"


def extract_ai_response_text(response, gemini_runtime: bool = False) -> str:
    """Extract string content from diverse OpenAI / Gemini SDK response objects."""
    if hasattr(response, "choices") and response.choices:
        choice = response.choices[0]
        if hasattr(choice, "message") and hasattr(choice.message, "content"):
            return choice.message.content or ""
    if hasattr(response, "text"):
        return response.text or ""
    if hasattr(response, "content"):
        return response.content or ""
    if hasattr(response, "output") and response.output:
        return str(response.output)
    return str(response)


def normalize_ai_correct_option(raw_value, options_len: int) -> Optional[int]:
    """Map AI response correct_option (integer, letter, or string) to valid 0-based index."""
    if isinstance(raw_value, int):
        return raw_value if 0 <= raw_value < options_len else None
    if isinstance(raw_value, str):
        val = raw_value.strip().upper()
        if val.isdigit():
            idx = int(val)
            return idx if 0 <= idx < options_len else None
        if len(val) == 1 and "A" <= val <= "Z":
            idx = ord(val) - ord("A")
            return idx if 0 <= idx < options_len else None
    return None


def clean_json_text(raw_text: str) -> str:
    """Extract raw JSON substring from markdown code blocks or wrappers."""
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def validate_ai_response(payload: dict, expected_count: int) -> List[Tuple[str, List[str], int, str]]:
    """Validate structure of AI JSON response."""
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
    settings: Optional[UserSettings] = None,
) -> List[Tuple[str, List[str], int, str]]:
    """Generate multiple choice questions using configured AI provider with fallback."""
    client = get_openai_client(settings)
    if client is None:
        if AI_OFFLINE_FALLBACK:
            generate_quizzes_with_ai.last_used_fallback = True  # type: ignore[attr-defined]
            return local_quiz_pack(payload, lang, count, mode)
        raise RuntimeError("AI is unavailable")
    
    generate_quizzes_with_ai.last_used_fallback = False  # type: ignore[attr-defined]

    runtime_provider = normalize_runtime_provider(settings)
    runtime_model = resolve_runtime_model(settings, model)
    count = max(1, min(10, count))
    payload = payload.strip()[:AI_MAX_SOURCE_CHARS]
    language_name = "Arabic" if lang == "ar" else "English"
    specialty_text = specialty.strip() or ("general education" if lang == "en" else "التعليم العام")
    diff = getattr(settings, "difficulty", "medium") if settings else "medium"
    if diff == "easy":
        diff_instruction = "Difficulty Level: Easy (focus on direct recall, clear facts, and foundational concepts)."
    elif diff == "hard":
        diff_instruction = "Difficulty Level: Hard (focus on deep analysis, complex application, edge cases, and critical thinking)."
    else:
        diff_instruction = "Difficulty Level: Medium (balanced mix of understanding and practical application)."

    system_prompt = (
        "You are an assessment designer. Return JSON only. "
        "Create high-quality multiple-choice questions with exactly 4 options each. "
        "Use zero-based integers for correct_option. "
        "Keep questions concise, avoid duplicate options, and keep explanations brief. "
        "Do not invent facts. If the source is insufficient, only ask about explicit content. "
        f"Return exactly {count} quizzes and do not stop early. "
        f"Act like a specialist in this domain: {specialty_text}. {diff_instruction} "
        "The response JSON must be an object with a single key named quizzes."
    )

    if mode == "topic":
        user_prompt = (
            f"Generate {count} MCQ quizzes in {language_name} from this topic:\n"
            f"{payload}\n\n"
            f"Return exactly {count} quizzes. "
            "Return JSON in this shape:\n"
            '{"quizzes":[{"question":"...","options":["...","...","...","..."],"correct_option":0,"explanation":"..."}]}'
        )
    else:
        user_prompt = (
            f"Read the following source text and create {count} MCQ quizzes in {language_name} "
            "that test understanding of the text.\n\n"
            f"Source text:\n{payload}\n\n"
            f"Return exactly {count} quizzes. "
            "Return JSON in this shape:\n"
            '{"quizzes":[{"question":"...","options":["...","...","...","..."],"correct_option":0,"explanation":"..."}]}'
        )

    def _run():
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if runtime_provider == "gemini":
            return client.chat.completions.create(
                model=runtime_model,
                messages=messages,
                temperature=0.1,
                max_tokens=min(8000, 1400 + count * 450),
            )

        kwargs = {
            "model": runtime_model,
            "store": False,
            "input": messages,
            "text": {"format": {"type": "json_object"}},
            "temperature": 0.1,
            "max_output_tokens": min(8000, 1400 + count * 450),
        }
        if not OPENAI_BASE_URL:
            kwargs["reasoning"] = {"effort": OPENAI_REASONING_EFFORT}
        try:
            return client.responses.create(**kwargs)
        except Exception:
            fallback_kwargs = {
                "model": model,
                "store": False,
                "input": messages,
            }
            try:
                return client.responses.create(**fallback_kwargs)
            except Exception:
                return client.chat.completions.create(
                    model=runtime_model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=min(8000, 1400 + count * 450),
                )

    try:
        response = await asyncio.to_thread(_run)
    except Exception as exc:
        if not AI_OFFLINE_FALLBACK:
            raise
        mark_ai_backend_failed(settings, model)
        logger.warning("AI quiz generation fallback used after provider error: %s", exc)
        generate_quizzes_with_ai.last_used_fallback = True  # type: ignore[attr-defined]
        return local_quiz_pack(payload, lang, count, mode)

    valid_items: List[Tuple[str, List[str], int, str]] = []
    try:
        raw_text = clean_json_text(extract_ai_response_text(response, gemini_runtime=(runtime_provider == "gemini")))
        payload_json = json.loads(raw_text)
        valid_items = validate_ai_response(payload_json, count)
        clear_ai_backend_failure(settings, model)
        if len(valid_items) >= count:
            return valid_items[:count]
    except Exception:
        pass

    fallback_used = True
    seen = {
        (
            question.strip().lower(),
            tuple(option.strip().lower() for option in options),
            correct_index,
        )
        for question, options, correct_index, _ in valid_items
    }
    for question, options, correct_index, explanation in local_quiz_pack(payload, lang, count, mode):
        signature = (
            question.strip().lower(),
            tuple(option.strip().lower() for option in options),
            correct_index,
        )
        if signature in seen:
            continue
        valid_items.append((question, options, correct_index, explanation))
        seen.add(signature)
        if len(valid_items) >= count:
            break
    generate_quizzes_with_ai.last_used_fallback = fallback_used  # type: ignore[attr-defined]
    return valid_items[:count]


def build_ai_tool_prompt(tool: str, payload: str, lang: str, specialty: str) -> Tuple[str, str, float, int]:
    raw_tool = (tool or "").strip().lower().replace(" ", "")
    selected = "trivia" if raw_tool == "trivia" else normalize_ai_tool(raw_tool)
    spec = AI_TOOL_SPECS.get(selected, AI_TOOL_SPECS["summary"])
    language_name = "Arabic" if lang == "ar" else "English"
    specialty_text = specialty.strip() or ("general education" if lang == "en" else "التعليم العام")
    system_prompt = (
        f"You are a precise educational assistant specialized in {specialty_text}. "
        f"Reply only in {language_name}. "
        f"Follow this task: {spec['instruction']}"
    )
    user_prompt = payload.strip() or ("Give a useful response." if lang == "en" else "قدّم رداً مفيداً.")
    return system_prompt, user_prompt, float(spec["temperature"]), int(spec["max_tokens"])


async def generate_ai_tool_text(
    tool: str,
    payload: str,
    lang: str,
    model: str,
    specialty: str = "",
    settings: Optional[UserSettings] = None,
) -> str:
    """Generate text using specialized AI tools (summary, flashcards, explanation, etc.)."""
    client = get_openai_client(settings)
    if client is None:
        if AI_OFFLINE_FALLBACK:
            generate_ai_tool_text.last_used_fallback = True  # type: ignore[attr-defined]
            return local_ai_tool_text(tool, payload, lang)
        raise RuntimeError("AI is unavailable")

    generate_ai_tool_text.last_used_fallback = False  # type: ignore[attr-defined]
    runtime_provider = normalize_runtime_provider(settings)
    runtime_model = resolve_runtime_model(settings, model)
    raw_tool = (tool or "").strip().lower().replace(" ", "")
    selected = "trivia" if raw_tool == "trivia" else normalize_ai_tool(raw_tool)
    if selected == "quiz":
        raise ValueError("quiz tool should use quiz flow")

    system_prompt, user_prompt, temperature, max_tokens = build_ai_tool_prompt(selected, payload, lang, specialty)

    def _run():
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        if runtime_provider == "gemini":
            return client.chat.completions.create(
                model=runtime_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        kwargs = {
            "model": runtime_model,
            "store": False,
            "input": messages,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }
        if not OPENAI_BASE_URL:
            kwargs["reasoning"] = {"effort": OPENAI_REASONING_EFFORT}
        try:
            return client.responses.create(**kwargs)
        except Exception:
            try:
                fallback_kwargs = {
                    "model": model,
                    "store": False,
                    "input": messages,
                }
                return client.responses.create(**fallback_kwargs)
            except Exception:
                return client.chat.completions.create(
                    model=runtime_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

    try:
        response = await asyncio.to_thread(_run)
    except Exception as exc:
        if not AI_OFFLINE_FALLBACK:
            raise
        mark_ai_backend_failed(settings, model)
        logger.warning("AI tool fallback used after provider error: %s", exc)
        generate_ai_tool_text.last_used_fallback = True  # type: ignore[attr-defined]
        return local_ai_tool_text(selected, payload, lang)

    try:
        text = clean_json_text(extract_ai_response_text(response, gemini_runtime=(runtime_provider == "gemini")))
        if not text:
            raise ValueError("AI returned empty text")
        clear_ai_backend_failure(settings, model)
        return text
    except Exception:
        generate_ai_tool_text.last_used_fallback = True  # type: ignore[attr-defined]
        return local_ai_tool_text(selected, payload, lang)
