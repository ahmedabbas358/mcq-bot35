"""
Command handlers for Telegram bot (/start, /help, /settings, /ai, /study, /export, /exam, etc.).
"""

import asyncio
import contextlib
import io
import logging
from typing import Optional

try:
    import telegram
    from telegram import Message, Update
    from telegram.constants import ChatType
    from telegram.ext import ContextTypes
except ImportError:
    telegram = None
    class _Mock:
        pass
    Message = _Mock
    Update = _Mock
    ChatType = _Mock
    ContextTypes = _Mock

from bot.config import (
    AI_DEFAULT_COUNT,
    AI_OFFLINE_FALLBACK,
    DEFAULT_DELETE_SOURCE,
    OPENAI_MODEL,
    PRESERVE_TARGET_ORDER,
    PUBLIC_BASE_URL,
    QUIZ_CONFIRMATION_MESSAGE,
    Target,
    UserSettings,
)
from bot.constants import (
    AI_PROVIDER_CATALOG,
    AI_TOOL_CATALOG,
    FREE_MODEL_CATALOG,
)
from bot.database import (
    DB,
    fetch_leaderboard,
    fetch_quiz,
    fetch_user_quizzes,
    get_user_settings,
    resolve_user_lang,
    update_user_settings,
)
from bot.i18n.texts import get_text
from bot.keyboards.inline import (
    build_ai_status_keyboard,
    build_controls_keyboard,
    build_count_keyboard,
    build_delivery_keyboard,
    build_difficulty_keyboard,
    build_exam_question_keyboard,
    build_export_keyboard,
    build_free_models_keyboard,
    build_fun_keyboard,
    build_language_keyboard,
    build_main_keyboard,
    build_provider_keyboard,
    build_share_keyboard,
    build_study_keyboard,
    build_tools_keyboard,
)
from bot.services.ai_service import (
    ai_backend_temporarily_disabled,
    ai_service_available,
    generate_ai_tool_text,
    generate_quizzes_with_ai,
    normalize_runtime_provider,
)
from bot.services.exam_service import (
    finish_exam,
    start_exam,
)
from bot.services.exporter import create_export_file
from bot.services.offline_ai import (
    local_ai_tool_text,
    local_quiz_pack,
    local_study_pack,
)
from bot.utils.helpers import (
    extract_message_text,
    format_target_label,
    humanize_ai_provider,
    humanize_ai_tool,
    humanize_delivery_mode,
    humanize_free_model,
    humanize_fun_interval,
    humanize_fun_style,
    humanize_language,
    humanize_share_mode,
    humanize_toggle,
    infer_ai_provider_from_model,
    infer_lang,
    normalize_ai_model_for_provider,
    normalize_ai_provider,
    normalize_ai_tool,
    normalize_free_model,
    normalize_fun_style,
    parse_ai_count_and_payload,
    parse_target_reference,
    resolve_ai_provider_choice,
    resolve_ai_tool_choice,
    resolve_chat_title,
    resolve_runtime_model,
    select_free_ai_profile,
    should_delete_source_message,
)
from bot.utils.queue_manager import (
    enqueue_mcq,
    enqueue_quiz_items,
    resolve_target_chat,
    send_queues,
)

logger = logging.getLogger(__name__)


async def send_text_reply(message: Message, text: str, **kwargs) -> None:
    """Send text reply safely ignoring exceptions."""
    with contextlib.suppress(Exception):
        await message.reply_text(text, **kwargs)


def build_settings_text(settings: UserSettings, lang: str) -> str:
    """Format settings dashboard text."""
    return get_text(
        "settings",
        lang,
        target=format_target_label(settings.default_target, settings.default_target_title, lang),
        delete_source=humanize_toggle(settings.delete_source, lang),
        ai_available=humanize_toggle(ai_service_available(settings) or AI_OFFLINE_FALLBACK, lang),
        offline_fallback=humanize_toggle(AI_OFFLINE_FALLBACK, lang),
        ai_enabled=humanize_toggle(settings.ai_enabled, lang),
        ai_model=settings.ai_model,
        ai_provider=humanize_ai_provider(settings.ai_provider, lang),
        ai_count=settings.ai_count,
        language=humanize_language(settings.preferred_language, lang),
        specialty=settings.ai_specialty or ("general" if lang == "en" else "عام"),
        ai_tool=humanize_ai_tool(settings.ai_tool_mode, lang),
        delivery_mode=humanize_delivery_mode(settings.delivery_mode, lang),
        share_mode=humanize_share_mode(settings.share_mode, lang),
        preserve_order=humanize_toggle(PRESERVE_TARGET_ORDER, lang),
        web_preview=humanize_toggle(bool(PUBLIC_BASE_URL), lang),
        show_explanation=humanize_toggle(settings.show_explanation, lang),
        confirmation=humanize_toggle(settings.confirmation_message, lang),
        fun_breaks=humanize_toggle(settings.fun_breaks, lang),
        fun_interval=humanize_fun_interval(settings.fun_interval, lang),
        fun_style=humanize_fun_style(settings.fun_style, lang),
    )


def build_ai_status_text(settings: UserSettings, lang: str) -> str:
    remote_ready = ai_service_available(settings)
    runtime_provider = normalize_runtime_provider(settings)
    runtime_model = resolve_runtime_model(settings, settings.ai_model)
    backend_cooling_down = ai_backend_temporarily_disabled(settings, settings.ai_model)
    if lang == "ar":
        lines = [
            get_text("ai_status_header", lang),
            f"- المزود الفعلي: {humanize_ai_provider(runtime_provider, lang)}",
            f"- النموذج الفعلي: {runtime_model}",
            f"- الذكاء الخارجي مكوَّن: {humanize_toggle(remote_ready, lang)}",
            f"- المحرك المحلي الاحتياطي: {humanize_toggle(AI_OFFLINE_FALLBACK, lang)}",
            f"- تخصص الذكاء: {settings.ai_specialty or 'عام'}",
            f"- اللغة المفضلة: {humanize_language(settings.preferred_language, lang)}",
        ]
        if remote_ready:
            lines.append("- عند توفر مزود حقيقي سيستخدمه البوت مباشرة.")
        elif backend_cooling_down:
            lines.append("- حدث فشل حديث في الخلفية، لذلك يستخدم البوت الاحتياطي مؤقتاً لتجنب التكرار البطيء.")
        elif AI_OFFLINE_FALLBACK:
            lines.append("- إذا لم يكن هناك مزود متصل، سيعمل البوت بمحرك محلي احتياطي للأسئلة وأدوات الدراسة.")
        else:
            lines.append("- لا يوجد مزود فعّال حالياً، فعّل Ollama أو اختر مزوداً متوافقاً من لوحة المزودات.")
        lines.append("")
        lines.append(get_text("free_ai_help", lang))
        return "\n".join(lines)

    lines = [
        get_text("ai_status_header", lang),
        f"- Active provider: {humanize_ai_provider(runtime_provider, lang)}",
        f"- Active model: {runtime_model}",
        f"- Remote AI configured: {humanize_toggle(remote_ready, lang)}",
        f"- Offline fallback engine: {humanize_toggle(AI_OFFLINE_FALLBACK, lang)}",
        f"- AI specialty: {settings.ai_specialty or 'general'}",
        f"- Preferred language: {humanize_language(settings.preferred_language, lang)}",
    ]
    if remote_ready:
        lines.append("- When a real provider is available, the bot uses it directly.")
    elif backend_cooling_down:
        lines.append("- A backend failed recently, so the bot is using the offline fallback temporarily to avoid repeated slow retries.")
    elif AI_OFFLINE_FALLBACK:
        lines.append("- If no provider is connected, the bot uses an offline engine for quizzes and study tools.")
    else:
        lines.append("- No live provider is configured right now. Enable Ollama or pick a compatible provider from the providers panel.")
    lines.append("")
    lines.append(get_text("free_ai_help", lang))
    return "\n".join(lines)


def build_providers_text(lang: str) -> str:
    lines = [get_text("providers_header", lang)]
    for code, meta in AI_PROVIDER_CATALOG.items():
        title = meta.get("ar" if lang == "ar" else "en", code)
        model = meta.get("default_model") or "custom"
        lines.append(f"• `{code}`: {title} ({model})")
    lines.append("")
    lines.append(get_text("usage_provider", lang))
    lines.append(get_text("usage_freeai", lang))
    return "\n".join(lines)


def build_tools_text(lang: str) -> str:
    lines = [get_text("tools_header", lang)]
    for code, meta in AI_TOOL_CATALOG.items():
        title = meta.get("ar" if lang == "ar" else "en", code)
        desc = meta.get("desc_ar" if lang == "ar" else "desc_en", "")
        lines.append(f"• `{code}`: {title} - {desc}")
    lines.append("")
    lines.append(get_text("usage_tool", lang))
    lines.append(get_text("usage_ask", lang))
    return "\n".join(lines)


def build_free_models_text(lang: str) -> str:
    lines = [get_text("free_models_header", lang)]
    for code, meta in FREE_MODEL_CATALOG.items():
        title = meta.get("ar" if lang == "ar" else "en", code)
        desc = meta.get("desc_ar" if lang == "ar" else "desc_en", "")
        rec = " ⭐" if meta.get("recommended") else ""
        lines.append(f"• `{code}`: {title}{rec}\n  {desc}")
    lines.append("")
    lines.append(get_text("usage_freemodel", lang))
    return "\n".join(lines)


def build_study_text(lang: str) -> str:
    return get_text("study_help", lang)


async def show_settings(message: Message, user_id: int, lang: str) -> None:
    settings = await get_user_settings(user_id)
    await send_text_reply(message, build_settings_text(settings, lang), reply_markup=build_controls_keyboard(lang, settings))


async def show_stats(message: Message, user_id: int, lang: str) -> None:
    conn = await DB.conn()
    settings = await get_user_settings(user_id)
    user_row = await (await conn.execute("SELECT sent FROM user_stats WHERE user_id=?", (user_id,))).fetchone()
    total_row = await (await conn.execute("SELECT COALESCE(SUM(sent), 0) AS sent FROM target_stats")).fetchone()
    text = get_text(
        "stats",
        lang,
        private_count=user_row["sent"] if user_row else 0,
        total_targets=total_row["sent"] if total_row else 0,
        target=format_target_label(settings.default_target, settings.default_target_title, lang),
    )
    await send_text_reply(message, text, reply_markup=build_main_keyboard(lang))


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    user = update.effective_user
    lang = await resolve_user_lang(user.id, getattr(user, "language_code", None), extract_message_text(message)) if user else "en"
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
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    await send_text_reply(message, get_text("help", lang), reply_markup=build_main_keyboard(lang))


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dedicated interactive navigation menu."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    await send_text_reply(message, get_text("menu_title", lang), reply_markup=build_main_keyboard(lang))


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


async def controls_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        if requested_target is None:
            await send_text_reply(message, get_text("usage_setchannel", lang))
            return
        target = requested_target
        resolved_chat = await resolve_target_chat(context.bot, target)
        title = resolve_chat_title(resolved_chat) if resolved_chat else str(target)

    await update_user_settings(user.id, default_target=target, default_target_title=title)
    await send_text_reply(message, get_text("target_set", lang, target=format_target_label(target, title, lang)))


async def publishhere_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    title = resolve_chat_title(chat)
    await update_user_settings(user.id, default_target=chat.id, default_target_title=title)
    await send_text_reply(message, get_text("target_set", lang, target=format_target_label(chat.id, title, lang)))


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
    new_value = not settings.delete_source
    await update_user_settings(user.id, delete_source=new_value)
    key = "delete_enabled" if new_value else "delete_disabled"
    await send_text_reply(message, get_text(key, lang))


async def toggleai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    new_value = not settings.ai_enabled
    await update_user_settings(user.id, ai_enabled=new_value)
    key = "toggle_ai_on" if new_value else "toggle_ai_off"
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
    inferred_provider = infer_ai_provider_from_model(model)
    normalized_model = normalize_ai_model_for_provider(inferred_provider, model)
    await update_user_settings(user.id, ai_model=normalized_model, ai_provider=inferred_provider, ai_enabled=True)
    await send_text_reply(message, get_text("model_set", lang, model=f"{normalized_model} ({inferred_provider})"))


async def providers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    await send_text_reply(message, build_providers_text(lang), reply_markup=build_provider_keyboard(lang, settings))


async def provider_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip()
    if not raw:
        await send_text_reply(message, get_text("usage_provider", lang))
        return
    provider = resolve_ai_provider_choice(raw)
    if provider is None:
        await send_text_reply(message, get_text("usage_provider", lang))
        return
    settings = await get_user_settings(user.id)
    suggested_model = AI_PROVIDER_CATALOG.get(provider, {}).get("default_model", "") or settings.ai_model
    if provider == "ollama" and (not suggested_model or suggested_model == OPENAI_MODEL):
        suggested_model = "qwen2.5:14b-instruct"
    await update_user_settings(user.id, ai_provider=provider, ai_model=suggested_model, ai_enabled=True)
    await send_text_reply(message, get_text("provider_set", lang, provider=humanize_ai_provider(provider, lang)))


async def freeai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    provider, model = select_free_ai_profile()
    updated = await update_user_settings(user.id, ai_provider=provider, ai_model=model, ai_enabled=True)
    await send_text_reply(message, build_ai_status_text(updated, lang), reply_markup=build_ai_status_keyboard(lang, updated))


async def aidiag_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    await send_text_reply(message, build_ai_status_text(settings, lang), reply_markup=build_ai_status_keyboard(lang, settings))


async def freemodels_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    await send_text_reply(message, build_free_models_text(lang), reply_markup=build_free_models_keyboard(lang, settings))


async def freemodel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip()
    if not raw:
        await send_text_reply(message, get_text("usage_freemodel", lang))
        return
    model = normalize_free_model(raw)
    if model is None:
        await send_text_reply(message, get_text("usage_freemodel", lang))
        return
    await update_user_settings(user.id, ai_provider="ollama", ai_model=model, ai_enabled=True)
    await send_text_reply(message, get_text("freemodel_set", lang, model=humanize_free_model(model, lang)))


async def setcount_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip()
    if not raw.isdigit():
        await send_text_reply(message, get_text("usage_setcount", lang))
        return
    count = max(1, min(10, int(raw)))
    await update_user_settings(user.id, ai_count=count)
    await send_text_reply(message, get_text("count_set", lang, count=count))


async def language_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip().lower()
    if raw not in {"auto", "ar", "en"}:
        await send_text_reply(message, get_text("usage_language", lang))
        return
    await update_user_settings(user.id, preferred_language=raw)
    await send_text_reply(message, get_text("language_set", raw if raw in {"ar", "en"} else lang, language=humanize_language(raw, raw if raw in {"ar", "en"} else lang)))


async def specialty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip()
    if not raw:
        await send_text_reply(message, get_text("usage_specialty", lang))
        return
    val = "" if raw.lower() in {"clear", "مسح", "افتراضي", "default"} else raw
    await update_user_settings(user.id, ai_specialty=val)
    await send_text_reply(message, get_text("specialty_set", lang, specialty=val or ("general" if lang == "en" else "عام")))


async def delivery_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip().lower()
    if raw not in {"fast", "rich"}:
        await send_text_reply(message, get_text("usage_delivery", lang))
        return
    await update_user_settings(user.id, delivery_mode=raw)
    await send_text_reply(message, get_text("delivery_set", lang, delivery=humanize_delivery_mode(raw, lang)))


async def examples_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    await send_text_reply(message, f"{get_text('examples', lang)}\n\n{get_text('free_ai_help', lang)}", reply_markup=build_main_keyboard(lang))


async def sharemode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip().lower()
    if raw not in {"telegram", "web", "both"}:
        await send_text_reply(message, get_text("usage_sharemode", lang))
        return
    await update_user_settings(user.id, share_mode=raw)
    await send_text_reply(message, get_text("sharemode_set", lang, mode=humanize_share_mode(raw, lang)))


async def toggleexplain_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    new_value = not settings.show_explanation
    await update_user_settings(user.id, show_explanation=new_value)
    key = "toggle_explain_on" if new_value else "toggle_explain_off"
    await send_text_reply(message, get_text(key, lang))


async def toggleconfirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    new_value = not settings.confirmation_message
    await update_user_settings(user.id, confirmation_message=new_value)
    key = "toggle_confirm_on" if new_value else "toggle_confirm_off"
    await send_text_reply(message, get_text(key, lang))


async def health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    total_queued = sum(q.qsize() for q in send_queues.values())
    text = get_text(
        "health",
        lang,
        queue_size=total_queued,
        ai_available=humanize_toggle(ai_service_available(settings) or AI_OFFLINE_FALLBACK, lang),
        web_preview=humanize_toggle(bool(PUBLIC_BASE_URL), lang),
        target_count=len(send_queues),
        active_senders=sum(len([t for t in tasks if not t.done()]) for tasks in send_queues.values()),
    )
    await send_text_reply(message, text)


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
        None, "", DEFAULT_DELETE_SOURCE, True, OPENAI_MODEL, "auto", AI_DEFAULT_COUNT, "auto", "", "rich", "both", True, QUIZ_CONFIRMATION_MESSAGE, "quiz", False, 6, "mixed"
    )
    if owner_user_id and not settings.ai_enabled:
        await send_text_reply(message, get_text("ai_disabled_user", lang))
        return

    count, clean_payload = parse_ai_count_and_payload(payload, settings.ai_count)
    if not clean_payload:
        await send_text_reply(message, get_text("ai_usage_topic" if mode == "topic" else "ai_usage_text", lang))
        return

    status_message = None
    remote_ai_ready = ai_service_available(settings)
    used_offline = not remote_ai_ready and AI_OFFLINE_FALLBACK
    with contextlib.suppress(Exception):
        processing_key = "ai_processing" if remote_ai_ready else ("ai_processing_offline" if AI_OFFLINE_FALLBACK else "ai_processing")
        status_message = await message.reply_text(get_text(processing_key, lang))

    try:
        if remote_ai_ready:
            quizzes = await generate_quizzes_with_ai(mode, clean_payload, lang, count, settings.ai_model, settings.ai_specialty, settings=settings)
            used_offline = used_offline or bool(getattr(generate_quizzes_with_ai, "last_used_fallback", False))
        elif AI_OFFLINE_FALLBACK:
            quizzes = local_quiz_pack(clean_payload, lang, count, mode)
        else:
            raise RuntimeError("AI is unavailable")
        target = explicit_target or settings.default_target or message.chat.id
        queued = await enqueue_quiz_items(
            target=target,
            quizzes=quizzes,
            context=context,
            owner_user_id=owner_user_id,
            lang=lang,
            source_chat_id=message.chat.id,
            source_message_id=message.message_id,
            delete_source=should_delete_source_message(settings.delete_source, message.chat.type, message.chat.id),
        )
        if status_message:
            with contextlib.suppress(Exception):
                done_text = get_text("ai_done", lang, count=queued)
                if used_offline:
                    done_text = f"{done_text}\n{get_text('ai_fallback_used', lang)}"
                await status_message.edit_text(done_text)
        elif queued:
            done_text = get_text("ai_done", lang, count=queued)
            if used_offline:
                done_text = f"{done_text}\n{get_text('ai_fallback_used', lang)}"
            await send_text_reply(message, done_text)
    except asyncio.QueueFull:
        if status_message:
            with contextlib.suppress(Exception):
                await status_message.edit_text(get_text("queue_full", lang))
        else:
            await send_text_reply(message, get_text("queue_full", lang))
    except Exception as exc:
        logger.exception("AI flow failed: %s", exc)
        if AI_OFFLINE_FALLBACK:
            try:
                quizzes = local_quiz_pack(clean_payload, lang, count, mode)
                target = explicit_target or settings.default_target or message.chat.id
                queued = await enqueue_quiz_items(
                    target=target,
                    quizzes=quizzes,
                    context=context,
                    owner_user_id=owner_user_id,
                    lang=lang,
                    source_chat_id=message.chat.id,
                    source_message_id=message.message_id,
                    delete_source=should_delete_source_message(settings.delete_source, message.chat.type, message.chat.id),
                )
                fallback_text = get_text("ai_done", lang, count=queued)
                fallback_text = f"{fallback_text}\n{get_text('ai_fallback_used', lang)}"
                if status_message:
                    with contextlib.suppress(Exception):
                        await status_message.edit_text(fallback_text)
                else:
                    await send_text_reply(message, fallback_text)
            except Exception:
                if status_message:
                    with contextlib.suppress(Exception):
                        await status_message.edit_text(get_text("ai_error", lang))
                else:
                    await send_text_reply(message, get_text("ai_error", lang))
        else:
            if status_message:
                with contextlib.suppress(Exception):
                    await status_message.edit_text(get_text("ai_error", lang))
            else:
                await send_text_reply(message, get_text("ai_error", lang))


async def ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    raw = " ".join(context.args).strip()
    lang = await resolve_user_lang(user.id, user.language_code, raw or extract_message_text(message))
    await run_ai_flow(message=message, context=context, owner_user_id=user.id, lang=lang, mode="topic", payload=raw)


async def quizify_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    raw = " ".join(context.args).strip()
    if not raw and message.reply_to_message:
        raw = extract_message_text(message.reply_to_message)
    lang = await resolve_user_lang(user.id, user.language_code, raw or extract_message_text(message))
    await run_ai_flow(message=message, context=context, owner_user_id=user.id, lang=lang, mode="quizify", payload=raw)


async def study_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    raw = " ".join(context.args).strip()
    if not raw and message.reply_to_message:
        raw = extract_message_text(message.reply_to_message)
    lang = await resolve_user_lang(user.id, user.language_code, raw or extract_message_text(message))
    settings = await get_user_settings(user.id)
    if not raw:
        await send_text_reply(message, build_study_text(lang), reply_markup=build_study_keyboard(lang, settings))
        return
    await run_ai_tool_flow(message=message, user_id=user.id, lang=lang, tool="studypack", payload=raw)


async def run_ai_tool_flow(*, message: Message, user_id: int, lang: str, tool: str, payload: str) -> None:
    settings = await get_user_settings(user_id)
    if not settings.ai_enabled and not AI_OFFLINE_FALLBACK:
        await send_text_reply(message, get_text("ai_disabled_user", lang))
        return

    selected_tool = normalize_ai_tool(tool)
    if selected_tool == "quiz":
        await run_ai_flow(message=message, context=None, owner_user_id=user_id, lang=lang, mode="quizify", payload=payload)  # type: ignore[arg-type]
        return

    remote_ready = ai_service_available(settings)
    used_offline = not remote_ready and AI_OFFLINE_FALLBACK
    status_message = None
    with contextlib.suppress(Exception):
        processing_key = "tool_processing" if remote_ready else ("tool_processing_offline" if AI_OFFLINE_FALLBACK else "tool_processing")
        status_message = await message.reply_text(get_text(processing_key, lang))

    try:
        if remote_ready:
            text = await generate_ai_tool_text(selected_tool, payload, lang, settings.ai_model, settings.ai_specialty, settings=settings)
            used_offline = used_offline or bool(getattr(generate_ai_tool_text, "last_used_fallback", False))
        elif AI_OFFLINE_FALLBACK:
            text = local_ai_tool_text(selected_tool, payload, lang)
        else:
            raise RuntimeError("AI is unavailable")

        if not text:
            raise ValueError("Tool returned empty text")

        out_text = f"🛠 *{humanize_ai_tool(selected_tool, lang)}*:\n\n{text}"
        if used_offline:
            out_text = f"{out_text}\n\n_{get_text('ai_fallback_used', lang)}_"

        if status_message:
            with contextlib.suppress(Exception):
                await status_message.edit_text(out_text, parse_mode="Markdown")
        else:
            await send_text_reply(message, out_text, parse_mode="Markdown")
    except Exception as exc:
        logger.exception("AI tool flow failed: %s", exc)
        if AI_OFFLINE_FALLBACK:
            try:
                fallback_text = local_ai_tool_text(selected_tool, payload, lang)
                out_text = f"🛠 *{humanize_ai_tool(selected_tool, lang)}*:\n\n{fallback_text}\n\n_{get_text('ai_fallback_used', lang)}_"
                if status_message:
                    with contextlib.suppress(Exception):
                        await status_message.edit_text(out_text, parse_mode="Markdown")
                else:
                    await send_text_reply(message, out_text, parse_mode="Markdown")
                return
            except Exception:
                pass
        if status_message:
            with contextlib.suppress(Exception):
                await status_message.edit_text(get_text("tool_error", lang))
        else:
            await send_text_reply(message, get_text("tool_error", lang))


async def tools_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    settings = await get_user_settings(user.id)
    await send_text_reply(message, build_tools_text(lang), reply_markup=build_tools_keyboard(lang, settings))


async def tool_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip()
    if not raw:
        await send_text_reply(message, get_text("usage_tool", lang))
        return
    tool = resolve_ai_tool_choice(raw)
    if tool is None:
        await send_text_reply(message, get_text("usage_tool", lang))
        return
    await update_user_settings(user.id, ai_tool_mode=tool)
    await send_text_reply(message, get_text("tool_set", lang, tool=humanize_ai_tool(tool, lang)))


async def ask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    settings = await get_user_settings(user.id)
    args = context.args or []
    if not args and message.reply_to_message:
        args = [extract_message_text(message.reply_to_message)]
    if not args:
        lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
        await send_text_reply(message, get_text("usage_ask", lang))
        return

    first_arg = args[0].strip().lower()
    tool_choice = resolve_ai_tool_choice(first_arg)
    if tool_choice and len(args) > 1:
        tool = tool_choice
        payload = " ".join(args[1:]).strip()
    else:
        tool = settings.ai_tool_mode or "explain"
        payload = " ".join(args).strip()

    lang = await resolve_user_lang(user.id, user.language_code, payload)
    await run_ai_tool_flow(message=message, user_id=user.id, lang=lang, tool=tool, payload=payload)


async def funmode_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip().lower()
    if raw not in {"on", "off"}:
        await send_text_reply(message, get_text("usage_funmode", lang))
        return
    val = raw == "on"
    await update_user_settings(user.id, fun_breaks=val)
    await send_text_reply(message, get_text("funmode_set", lang, status=humanize_toggle(val, lang)))


async def funrate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip()
    if not raw.isdigit():
        await send_text_reply(message, get_text("usage_funrate", lang))
        return
    rate = max(1, min(30, int(raw)))
    await update_user_settings(user.id, fun_interval=rate)
    await send_text_reply(message, get_text("funrate_set", lang, rate=rate))


async def funstyle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip().lower()
    style = normalize_fun_style(raw)
    await update_user_settings(user.id, fun_style=style)
    await send_text_reply(message, get_text("funstyle_set", lang, style=humanize_fun_style(style, lang)))


# ---------------- NEW FEATURE HANDLERS ---------------- #

async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /export command to export quizzes to files."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    
    # Check if a format arg was given directly: e.g. /export csv
    fmt_arg = context.args[0].lower() if context.args else ""
    if fmt_arg in {"json", "csv", "gift", "text", "txt", "moodle", "anki"}:
        quizzes = await fetch_user_quizzes(user.id, limit=200)
        if not quizzes:
            await send_text_reply(message, get_text("export_empty", lang))
            return
        filename, data = create_export_file(quizzes, fmt_arg)
        file_obj = io.BytesIO(data)
        file_obj.name = filename
        caption = get_text("export_success", lang, count=len(quizzes))
        with contextlib.suppress(Exception):
            await message.reply_document(document=file_obj, caption=caption)
        return

    await send_text_reply(message, get_text("export_help", lang), reply_markup=build_export_keyboard(lang), parse_mode="Markdown")


async def exam_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /exam or /test to launch interactive testing."""
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))

    # Fetch recent quizzes
    quizzes = await fetch_user_quizzes(user.id, limit=20)
    if not quizzes:
        # Fallback to general recent quizzes
        quizzes = await fetch_user_quizzes(None, limit=10)
    
    if not quizzes:
        await send_text_reply(message, get_text("export_empty", lang))
        return

    # Take up to 10 questions for exam
    exam_subset = quizzes[:10]
    session = start_exam(user.id, chat.id, exam_subset, lang)
    if not session:
        return

    intro = get_text("exam_intro", lang, count=len(exam_subset))
    q0 = session.quizzes[0]
    keyboard = build_exam_question_keyboard(q0["options"], 0, len(exam_subset), lang)
    
    q_text = f"🎯 *السؤال 1 من {len(exam_subset)}:*\n\n{q0['question']}" if lang == "ar" else f"🎯 *Question 1 of {len(exam_subset)}:*\n\n{q0['question']}"
    await send_text_reply(message, f"{intro}\n\n{q_text}", reply_markup=keyboard, parse_mode="Markdown")


async def format_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send ready-to-copy MCQ format template."""
    message = update.effective_message
    if not message:
        return
    user = update.effective_user
    lang = await resolve_user_lang(user.id if user else 0, getattr(user, "language_code", None), "")
    if lang == "ar":
        text = (
            "📋 *نموذج كتابة وتنسيق الأسئلة (جاهز للنسخ والتعديل):*\n\n"
            "```text\n"
            "س1: ما هي عاصمة المملكة العربية السعودية؟\n"
            "أ) الرياض\n"
            "ب) جدة\n"
            "ج) مكة المكرمة\n"
            "د) المدينة المنورة\n"
            "الإجابة: أ\n"
            "الشرح: الرياض هي العاصمة الرسمية وأكبر مدن المملكة.\n\n"
            "س2: تشرق الشمس من الشرق دائماً.\n"
            "الإجابة: صح\n"
            "الشرح: دوران الأرض حول محورها من الغرب إلى الشرق.\n"
            "```\n\n"
            "💡 *نصائح ذكية للمجموعات والامتحانات:*\n"
            "• يمكنك إرسال أكثر من سؤال في رسالة واحدة وسينشرها البوت تباعاً.\n"
            "• يمكنك وضع إيموجي ✅ بجوار الخيار الصحيح بدلاً من كتابة سطر الإجابة.\n"
            "• يدعم البوت أيضاً الترقيم الإنجليزي (A, B, C, D) أو (1, 2, 3, 4) أو النقط (- •)."
        )
    else:
        text = (
            "📋 *Copyable MCQ Format Template:*\n\n"
            "```text\n"
            "Q1: What is the capital of France?\n"
            "A) Berlin\n"
            "B) Madrid\n"
            "C) Paris\n"
            "D) Rome\n"
            "Answer: C\n"
            "Explanation: Paris is the capital and largest city of France.\n\n"
            "Q2: Water boils at 100 degrees Celsius.\n"
            "Answer: True\n"
            "Explanation: Under standard atmospheric pressure.\n"
            "```\n\n"
            "💡 *Smart tips:*\n"
            "• You can send multiple questions in a single message.\n"
            "• You can put a checkmark ✅ next to the correct choice.\n"
            "• Numbers, letters, or bullet points are all recognized."
        )
    await send_text_reply(message, text, parse_mode="Markdown")


async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Display leaderboard for interactive exam participants."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not message or not chat:
        return
    lang = await resolve_user_lang(user.id if user else 0, getattr(user, "language_code", None), "")
    
    chat_filter = chat.id if chat.type in {ChatType.GROUP, ChatType.SUPERGROUP} else None
    rows = await fetch_leaderboard(chat_filter, limit=10)
    
    if not rows:
        empty_text = "🏆 *لوحة الشرف:*\n\nلا توجد نتائج مسجلة حتى الآن. ابدأ اختباراً الآن عبر /exam لتكون أول المتصدرين!" if lang == "ar" else "🏆 *Leaderboard:*\n\nNo exam results yet. Take an exam with /exam to be first!"
        await send_text_reply(message, empty_text, parse_mode="Markdown")
        return

    medals = ["🥇", "🥈", "🥉"] + ["🎖"] * 7
    title = "🏆 *لوحة الشرف لأعلى النتائج:*" if lang == "ar" else "🏆 *Top Performers Leaderboard:*"
    lines = [title, ""]
    for idx, r in enumerate(rows):
        medal = medals[idx] if idx < len(medals) else "•"
        uname = r.get("user_name", "Anonymous")
        pct = r.get("percentage", 0)
        score = r.get("score", 0)
        total = r.get("total", 0)
        dur = r.get("duration", 0)
        dur_str = f"{dur}ث" if lang == "ar" else f"{dur}s"
        lines.append(f"{medal} *{idx + 1}. {uname}* — {pct}% ({score}/{total}) في {dur_str}")

    await send_text_reply(message, "\n".join(lines), parse_mode="Markdown")


async def difficulty_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set AI difficulty level (/difficulty <easy|medium|hard>)."""
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return
    lang = await resolve_user_lang(user.id, user.language_code, extract_message_text(message))
    raw = " ".join(context.args).strip().lower()
    levels = {"easy": "easy", "medium": "medium", "hard": "hard", "سهل": "easy", "متوسط": "medium", "صعب": "hard", "متقدم": "hard"}
    if raw not in levels:
        settings = await get_user_settings(user.id)
        cur = getattr(settings, "difficulty", "medium")
        prompt = (
            f"🎯 *مستوى الصعوبة الحالي:* `{cur}`\n\n"
            "لتغيير المستوى، أرسل:\n"
            "• `/difficulty easy` (سهل)\n"
            "• `/difficulty medium` (متوسط)\n"
            "• `/difficulty hard` (صعب / متقدم)"
            if lang == "ar"
            else
            f"🎯 *Current Difficulty Level:* `{cur}`\n\n"
            "To change, send:\n"
            "• `/difficulty easy`\n"
            "• `/difficulty medium`\n"
            "• `/difficulty hard`"
        )
        await send_text_reply(message, prompt, reply_markup=build_difficulty_keyboard(lang, settings), parse_mode="Markdown")
        return

    chosen = levels[raw]
    await update_user_settings(user.id, difficulty=chosen)
    confirm = f"✅ تم ضبط مستوى الصعوبة إلى: *{chosen}*" if lang == "ar" else f"✅ Difficulty level set to: *{chosen}*"
    await send_text_reply(message, confirm, parse_mode="Markdown")
