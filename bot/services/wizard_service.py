"""
Interactive Question Creation Wizard Service.
Manages step-by-step state machine for crafting MCQs with zero syntax required.
"""

from dataclasses import dataclass, field
import time
from typing import Dict, List, Optional


@dataclass
class QuestionWizard:
    user_id: int
    chat_id: int
    step: str  # "question", "options", "correct", "explanation"
    question: str = ""
    options: List[str] = field(default_factory=list)
    correct_option: int = 0
    explanation: str = ""
    lang: str = "ar"
    created_at: float = field(default_factory=time.time)


_active_wizards: Dict[int, QuestionWizard] = {}


def start_wizard(user_id: int, chat_id: int, lang: str = "ar") -> QuestionWizard:
    """Initialize a new wizard session for user."""
    wizard = QuestionWizard(
        user_id=user_id,
        chat_id=chat_id,
        step="question",
        lang=lang,
    )
    _active_wizards[user_id] = wizard
    return wizard


def get_wizard(user_id: int) -> Optional[QuestionWizard]:
    """Retrieve active wizard if not expired (15-min timeout)."""
    wizard = _active_wizards.get(user_id)
    if wizard and (time.time() - wizard.created_at) < 900:
        return wizard
    if wizard:
        cancel_wizard(user_id)
    return None


def cancel_wizard(user_id: int) -> None:
    """Discard active wizard."""
    _active_wizards.pop(user_id, None)


async def finish_wizard_and_publish(wizard: QuestionWizard, message, context) -> None:
    """Publish the wizard quiz and show completion card."""
    from bot.database import get_user_settings
    from bot.utils.helpers import format_target_label
    from bot.utils.queue_manager import enqueue_quiz_items
    from bot.keyboards.inline import build_wizard_completed_keyboard

    settings = await get_user_settings(wizard.user_id)
    target = settings.default_target if settings and settings.default_target else wizard.chat_id
    quizzes = [(wizard.question, wizard.options, wizard.correct_option, wizard.explanation)]
    await enqueue_quiz_items(
        target=target,
        quizzes=quizzes,
        context=context,
        owner_user_id=wizard.user_id,
        lang=wizard.lang,
        source_chat_id=None,
        source_message_id=None,
        delete_source=False,
    )
    cancel_wizard(wizard.user_id)
    correct_text = wizard.options[wizard.correct_option] if 0 <= wizard.correct_option < len(wizard.options) else ""
    correct_letter = chr(65 + wizard.correct_option)
    expl_part = f"\n💡 *الشرح:* {wizard.explanation}" if wizard.explanation else ""
    target_str = format_target_label(target, settings.default_target_title if settings else "", wizard.lang)
    if wizard.lang == "ar":
        confirm_text = (
            "🎉 *تم إنشاء ونشر السؤال بنجاح!* ✨\n"
            "───────────────────────\n"
            f"📌 *السؤال:* {wizard.question}\n"
            f"✅ *الإجابة الصحيحة:* {correct_letter}) {correct_text}{expl_part}\n"
            f"🎯 *الوجهة:* {target_str}"
        )
    else:
        confirm_text = (
            "🎉 *Question Created & Published Successfully!* ✨\n"
            "───────────────────────\n"
            f"📌 *Question:* {wizard.question}\n"
            f"✅ *Correct Answer:* {correct_letter}) {correct_text}{expl_part}\n"
            f"🎯 *Target:* {target_str}"
        )
    keyboard = build_wizard_completed_keyboard(wizard.lang)
    try:
        await message.reply_text(confirm_text, reply_markup=keyboard, parse_mode="Markdown")
    except Exception:
        pass
