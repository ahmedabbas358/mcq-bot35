"""
Callback query handler for inline menus, panel toggles, settings, explanations, and exams.
"""

import asyncio
import contextlib
import io
import logging

try:
    from telegram import Update
    from telegram.ext import ContextTypes
except ImportError:
    class _Mock:
        pass
    Update = _Mock
    ContextTypes = _Mock

from bot.config import (
    OPENAI_MODEL,
    UserSettings,
)
from bot.constants import AI_PROVIDER_CATALOG
from bot.database import (
    DB,
    fetch_quiz,
    fetch_user_quizzes,
    get_user_settings,
    resolve_user_lang,
    save_exam_result,
    update_user_settings,
)
from bot.handlers.commands import (
    build_ai_status_text,
    build_free_models_text,
    build_providers_text,
    build_settings_text,
    build_study_text,
    build_tools_text,
    exam_handler,
    format_handler,
    leaderboard_handler,
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
from bot.services.exam_service import (
    finish_exam,
    get_exam,
)
from bot.services.exporter import create_export_file
from bot.utils.helpers import (
    format_target_label,
    normalize_free_model,
    normalize_fun_style,
    resolve_ai_provider_choice,
    resolve_ai_tool_choice,
    select_free_ai_profile,
)
from bot.utils.queue_manager import enqueue_quiz_items

logger = logging.getLogger(__name__)


async def edit_panel(query, user_id: int, lang: str, panel: str) -> None:
    """Edit message text and keyboard according to requested sub-panel."""
    settings = await get_user_settings(user_id)
    panel_map = {
        "settings": (build_settings_text(settings, lang), build_controls_keyboard(lang, settings)),
        "language": (get_text("usage_language", lang), build_language_keyboard(lang, settings)),
        "delivery": (get_text("usage_delivery", lang), build_delivery_keyboard(lang, settings)),
        "share": (get_text("usage_sharemode", lang), build_share_keyboard(lang, settings)),
        "count": (get_text("usage_setcount", lang), build_count_keyboard(lang, settings)),
        "providers": (build_providers_text(lang), build_provider_keyboard(lang, settings)),
        "freemodels": (build_free_models_text(lang), build_free_models_keyboard(lang, settings)),
        "tools": (build_tools_text(lang), build_tools_keyboard(lang, settings)),
        "study": (build_study_text(lang), build_study_keyboard(lang, settings)),
        "fun": (get_text("fun_help", lang), build_fun_keyboard(lang, settings)),
        "ai": (build_ai_status_text(settings, lang), build_ai_status_keyboard(lang, settings)),
        "export": (get_text("export_help", lang), build_export_keyboard(lang)),
        "difficulty": (get_text("usage_difficulty", lang), build_difficulty_keyboard(lang, settings)),
    }
    if panel not in panel_map:
        return
    text, keyboard = panel_map[panel]
    with contextlib.suppress(Exception):
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    with contextlib.suppress(Exception):
        await query.answer()

    data = query.data or ""
    user = update.effective_user
    chat = update.effective_chat
    lang = await resolve_user_lang(user.id, getattr(user, "language_code", None), "") if user else "en"

    # Navigation & Home
    if data in {"home", "start"}:
        with contextlib.suppress(Exception):
            await query.edit_message_text(get_text("start", lang), reply_markup=build_main_keyboard(lang))
        return
    if data == "help":
        with contextlib.suppress(Exception):
            await query.edit_message_text(get_text("help", lang), reply_markup=build_main_keyboard(lang))
        return
    if data == "examples":
        with contextlib.suppress(Exception):
            await query.edit_message_text(f"{get_text('examples', lang)}\n\n{get_text('free_ai_help', lang)}", reply_markup=build_main_keyboard(lang))
        return
    if data == "cmd:exam":
        await exam_handler(update, context)
        return
    if data == "cmd:leaderboard":
        await leaderboard_handler(update, context)
        return
    if data == "cmd:format":
        await format_handler(update, context)
        return
    if data == "tools" and user:
        await edit_panel(query, user.id, lang, "tools")
        return
    if data == "study" and user:
        await edit_panel(query, user.id, lang, "study")
        return
    if data == "providers" and user:
        await edit_panel(query, user.id, lang, "providers")
        return
    if data == "freemodels" and user:
        await edit_panel(query, user.id, lang, "freemodels")
        return
    if data == "fun" and user:
        await edit_panel(query, user.id, lang, "fun")
        return

    # Free AI shortcut
    if data == "freeai" and user:
        provider, model = select_free_ai_profile()
        updated = await update_user_settings(user.id, ai_provider=provider, ai_model=model, ai_enabled=True)
        with contextlib.suppress(Exception):
            await query.edit_message_text(build_ai_status_text(updated, lang), reply_markup=build_ai_status_keyboard(lang, updated))
        return

    # Panels
    if data == "settings" or data.startswith("panel:"):
        panel = "settings" if data == "settings" else data.split(":", 1)[1]
        if user:
            await edit_panel(query, user.id, lang, panel)
        return

    # Toggles
    if data.startswith("toggle:") and user:
        action = data.split(":", 1)[1]
        settings = await get_user_settings(user.id)
        if action == "ai":
            await update_user_settings(user.id, ai_enabled=not settings.ai_enabled)
        elif action == "delete":
            await update_user_settings(user.id, delete_source=not settings.delete_source)
        elif action == "explain":
            await update_user_settings(user.id, show_explanation=not settings.show_explanation)
        elif action == "confirm":
            await update_user_settings(user.id, confirmation_message=not settings.confirmation_message)
        elif action == "fun":
            await update_user_settings(user.id, fun_breaks=not settings.fun_breaks)
        
        updated = await get_user_settings(user.id)
        with contextlib.suppress(Exception):
            await query.edit_message_text(build_settings_text(updated, lang), reply_markup=build_controls_keyboard(lang, updated))
        return

    # Set parameters
    if data.startswith("set:") and user:
        parts = data.split(":", 2)
        if len(parts) == 3:
            _, kind, value = parts
            if kind == "language" and value in {"auto", "ar", "en"}:
                await update_user_settings(user.id, preferred_language=value)
                await edit_panel(query, user.id, value if value in {"ar", "en"} else lang, "language")
                return
            if kind == "delivery" and value in {"fast", "rich"}:
                await update_user_settings(user.id, delivery_mode=value)
                await edit_panel(query, user.id, lang, "delivery")
                return
            if kind == "share" and value in {"telegram", "web", "both"}:
                await update_user_settings(user.id, share_mode=value)
                await edit_panel(query, user.id, lang, "share")
                return
            if kind == "count" and value.isdigit():
                await update_user_settings(user.id, ai_count=max(1, min(10, int(value))))
                await edit_panel(query, user.id, lang, "count")
                return
            if kind == "provider":
                provider = resolve_ai_provider_choice(value)
                if provider:
                    cur = await get_user_settings(user.id)
                    sugg_model = AI_PROVIDER_CATALOG.get(provider, {}).get("default_model", "") or cur.ai_model
                    if provider == "ollama" and (not sugg_model or sugg_model == OPENAI_MODEL):
                        sugg_model = "qwen2.5:14b-instruct"
                    await update_user_settings(user.id, ai_provider=provider, ai_model=sugg_model, ai_enabled=True)
                    await edit_panel(query, user.id, lang, "providers")
                return
            if kind == "freemodel":
                model = normalize_free_model(value)
                if model:
                    await update_user_settings(user.id, ai_provider="ollama", ai_model=model, ai_enabled=True)
                    await edit_panel(query, user.id, lang, "freemodels")
                return
            if kind in {"tool", "studytool"}:
                tool = resolve_ai_tool_choice(value)
                if tool:
                    await update_user_settings(user.id, ai_tool_mode=tool)
                    await edit_panel(query, user.id, lang, "study" if kind == "studytool" else "tools")
                return
            if kind == "funstyle":
                await update_user_settings(user.id, fun_style=normalize_fun_style(value))
                await edit_panel(query, user.id, lang, "fun")
                return
            if kind == "funrate" and value.isdigit():
                await update_user_settings(user.id, fun_interval=max(1, min(30, int(value))))
                await edit_panel(query, user.id, lang, "fun")
                return
            if kind == "difficulty" and value in {"easy", "medium", "hard"}:
                await update_user_settings(user.id, difficulty=value)
                await edit_panel(query, user.id, lang, "difficulty")
                return

    # Stats
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

    # Repost quiz
    if data.startswith("repost:") and query.message:
        quiz_id = data.split(":", 1)[1]
        quiz = await fetch_quiz(quiz_id)
        if quiz is None:
            with contextlib.suppress(Exception):
                await query.answer(get_text("quiz_missing", lang), show_alert=True)
            return
        question, options, correct_option, explanation, owner_user_id = quiz
        try:
            owner_settings = await get_user_settings(owner_user_id) if owner_user_id else None
            target = owner_settings.default_target if owner_settings and owner_settings.default_target else query.message.chat.id
            await enqueue_quiz_items(
                target=target,
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

    # Explain popup/message
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
            await query.message.reply_text(f"💡 *{'التفسير' if lang == 'ar' else 'Explanation'}:*\n\n{explanation}", parse_mode="Markdown")
        return

    # Export formats
    if data.startswith("export:") and user and query.message:
        fmt = data.split(":", 1)[1]
        quizzes = await fetch_user_quizzes(user.id, limit=200)
        if not quizzes:
            with contextlib.suppress(Exception):
                await query.answer(get_text("export_empty", lang), show_alert=True)
            return
        filename, data_bytes = create_export_file(quizzes, fmt)
        file_obj = io.BytesIO(data_bytes)
        file_obj.name = filename
        caption = get_text("export_success", lang, count=len(quizzes))
        with contextlib.suppress(Exception):
            await query.message.reply_document(document=file_obj, caption=caption)
        return

    # Interactive Exam
    if data.startswith("exam_ans:") and user and chat and query.message:
        session = get_exam(user.id, chat.id)
        if not session:
            with contextlib.suppress(Exception):
                await query.answer("انتهى الاختبار أو انتهت صلاحيته." if lang == "ar" else "Exam expired or finished.", show_alert=True)
            return
        parts = data.split(":")
        q_idx = int(parts[1])
        chosen_opt = int(parts[2])
        if q_idx != session.current_index:
            return

        cur_quiz = session.quizzes[q_idx]
        correct_idx = cur_quiz.get("correct_option", 0)
        is_correct = chosen_opt == correct_idx
        if is_correct:
            session.correct_count += 1
            feedback_note = "✅ إجابة صحيحة!" if session.lang == "ar" else "✅ Correct!"
        else:
            correct_label = chr(65 + correct_idx)
            feedback_note = f"❌ إجابة خاطئة. الصحيح: {correct_label}" if session.lang == "ar" else f"❌ Wrong. Correct: {correct_label}"

        with contextlib.suppress(Exception):
            await query.answer(feedback_note, show_alert=False)

        # Move to next question or finish
        session.current_index += 1
        if session.current_index >= len(session.quizzes):
            # Finish exam
            summary = finish_exam(user.id, chat.id)
            if summary:
                with contextlib.suppress(Exception):
                    await save_exam_result(
                        user_id=user.id,
                        chat_id=chat.id,
                        user_name=user.full_name or getattr(user, "username", "") or "Anonymous",
                        score=summary["correct"],
                        total=summary["total"],
                        percentage=summary["score"],
                        duration=summary.get("duration", 0),
                    )
                result_text = get_text(
                    "exam_finished",
                    session.lang,
                    total=summary["total"],
                    correct=summary["correct"],
                    wrong=summary["wrong"],
                    score=summary["score"],
                    feedback=summary["feedback"],
                )
                with contextlib.suppress(Exception):
                    await query.edit_message_text(result_text, reply_markup=build_main_keyboard(session.lang), parse_mode="Markdown")
            return

        # Show next question
        next_idx = session.current_index
        next_quiz = session.quizzes[next_idx]
        keyboard = build_exam_question_keyboard(next_quiz["options"], next_idx, len(session.quizzes), session.lang)
        next_text = f"🎯 *السؤال {next_idx + 1} من {len(session.quizzes)}:*\n\n{next_quiz['question']}" if session.lang == "ar" else f"🎯 *Question {next_idx + 1} of {len(session.quizzes)}:*\n\n{next_quiz['question']}"
        with contextlib.suppress(Exception):
            await query.edit_message_text(next_text, reply_markup=keyboard, parse_mode="Markdown")
        return

    if data == "exam_quit" and user and chat:
        finish_exam(user.id, chat.id)
        with contextlib.suppress(Exception):
            await query.edit_message_text("❌ تم إنهاء الاختبار." if lang == "ar" else "❌ Exam terminated.", reply_markup=build_main_keyboard(lang))
        return

    with contextlib.suppress(Exception):
        await query.answer(get_text("unsupported", lang), show_alert=True)
