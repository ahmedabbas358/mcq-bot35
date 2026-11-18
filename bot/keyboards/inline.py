"""
Inline keyboard builders for interactive panels, settings, study tools, quizzes, and exports.
"""

from typing import Dict, List, Optional
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    class InlineKeyboardButton:
        def __init__(self, text, **kwargs):
            self.text = text
            self.__dict__.update(kwargs)
    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

from bot.config import (
    ENABLE_WEB_PREVIEW,
    PUBLIC_BASE_URL,
    UserSettings,
)
from bot.constants import (
    AI_PROVIDER_CATALOG,
    AI_TOOL_CATALOG,
    FREE_MODEL_CATALOG,
)
from bot.i18n.texts import get_text
from bot.utils.helpers import (
    format_target_label,
    humanize_ai_provider,
    humanize_ai_tool,
    humanize_delivery_mode,
    humanize_difficulty,
    humanize_free_model,
    humanize_fun_interval,
    humanize_fun_style,
    humanize_language,
    humanize_share_mode,
    humanize_toggle,
    normalize_ai_provider,
    normalize_ai_tool,
    normalize_free_model,
    normalize_fun_style,
    quote_plus,
)


def _selected_label(label: str, selected: bool) -> str:
    return f"• {label} •" if selected else label


def _back_keyboard(lang: str, back_to: str = "settings") -> InlineKeyboardMarkup:
    back_label = "« رجوع" if lang == "ar" else "« Back"
    return InlineKeyboardMarkup([[InlineKeyboardButton(back_label, callback_data=f"panel:{back_to}")]])


def build_main_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    """Build the home quick-access keyboard."""
    if lang == "ar":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✍️ إنشاء سؤال جديد", callback_data="cmd:create"),
                InlineKeyboardButton("🎯 بدء اختبار تفاعلي", callback_data="cmd:exam"),
            ],
            [
                InlineKeyboardButton("🤖 توليد بالذكاء الاصطناعي", callback_data="panel:ai"),
                InlineKeyboardButton("📚 أدوات المذاكرة", callback_data="study"),
            ],
            [
                InlineKeyboardButton("🏆 لوحة المتصدرين", callback_data="cmd:leaderboard"),
                InlineKeyboardButton("📤 تصدير بنك الأسئلة", callback_data="panel:export"),
            ],
            [
                InlineKeyboardButton("📋 نموذج التنسيق", callback_data="cmd:format"),
                InlineKeyboardButton("⚙️ الإعدادات والتحكم", callback_data="settings"),
            ],
            [
                InlineKeyboardButton("📊 الإحصائيات", callback_data="stats"),
                InlineKeyboardButton("❓ دليل المساعدة الشامل", callback_data="help"),
            ],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✍️ Create Question", callback_data="cmd:create"),
            InlineKeyboardButton("🎯 Start Exam", callback_data="cmd:exam"),
        ],
        [
            InlineKeyboardButton("🤖 AI Generator", callback_data="panel:ai"),
            InlineKeyboardButton("📚 Study Pack", callback_data="study"),
        ],
        [
            InlineKeyboardButton("🏆 Leaderboard", callback_data="cmd:leaderboard"),
            InlineKeyboardButton("📤 Export Quizzes", callback_data="panel:export"),
        ],
        [
            InlineKeyboardButton("📋 Copyable Template", callback_data="cmd:format"),
            InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="stats"),
            InlineKeyboardButton("❓ Help Guide", callback_data="help"),
        ],
    ])


def build_wizard_cancel_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    label = "❌ إلغاء الإنشاء" if lang == "ar" else "❌ Cancel"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="wiz:cancel")]])


def build_wizard_choices_keyboard(options: List[str], lang: str = "ar") -> InlineKeyboardMarkup:
    rows = []
    row = []
    for idx, opt in enumerate(options):
        letter = chr(65 + idx)
        short_opt = opt[:22] + "…" if len(opt) > 22 else opt
        btn_label = f"{letter}) {short_opt}"
        row.append(InlineKeyboardButton(btn_label, callback_data=f"wiz_ans:{idx}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    cancel_label = "❌ إلغاء" if lang == "ar" else "❌ Cancel"
    rows.append([InlineKeyboardButton(cancel_label, callback_data="wiz:cancel")])
    return InlineKeyboardMarkup(rows)


def build_wizard_skip_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    skip_label = "⏭ تخطي الشرح ونشر السؤال" if lang == "ar" else "⏭ Skip & Publish"
    cancel_label = "❌ إلغاء" if lang == "ar" else "❌ Cancel"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(skip_label, callback_data="wiz:skip_explanation")],
        [InlineKeyboardButton(cancel_label, callback_data="wiz:cancel")],
    ])


def build_wizard_completed_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    another_label = "➕ إنشاء سؤال آخر" if lang == "ar" else "➕ Create Another"
    home_label = "🏠 القائمة الرئيسية" if lang == "ar" else "🏠 Main Menu"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(another_label, callback_data="cmd:create"),
            InlineKeyboardButton(home_label, callback_data="home"),
        ]
    ])


def build_difficulty_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = getattr(settings, "difficulty", "medium")
    is_ar = lang == "ar"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_selected_label("سهل 🟢" if is_ar else "Easy 🟢", cur == "easy"), callback_data="set:difficulty:easy"),
            InlineKeyboardButton(_selected_label("متوسط 🟡" if is_ar else "Medium 🟡", cur == "medium"), callback_data="set:difficulty:medium"),
            InlineKeyboardButton(_selected_label("متقدم 🔴" if is_ar else "Hard 🔴", cur == "hard"), callback_data="set:difficulty:hard"),
        ],
        [InlineKeyboardButton("« رجوع" if is_ar else "« Back", callback_data="panel:settings")],
    ])


def build_controls_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    """Build the settings dashboard panel."""
    is_ar = lang == "ar"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"🤖 الذكاء: {humanize_toggle(settings.ai_enabled, lang)}" if is_ar else f"🤖 AI: {humanize_toggle(settings.ai_enabled, lang)}", callback_data="toggle:ai"),
            InlineKeyboardButton(f"🗑 مسح الأصل: {humanize_toggle(settings.delete_source, lang)}" if is_ar else f"🗑 Delete src: {humanize_toggle(settings.delete_source, lang)}", callback_data="toggle:delete"),
        ],
        [
            InlineKeyboardButton(f"💡 التفسير: {humanize_toggle(settings.show_explanation, lang)}" if is_ar else f"💡 Explain: {humanize_toggle(settings.show_explanation, lang)}", callback_data="toggle:explain"),
            InlineKeyboardButton(f"📩 التأكيد: {humanize_toggle(settings.confirmation_message, lang)}" if is_ar else f"📩 Confirm: {humanize_toggle(settings.confirmation_message, lang)}", callback_data="toggle:confirm"),
        ],
        [
            InlineKeyboardButton(f"🌐 اللغة: {humanize_language(settings.preferred_language, lang)}" if is_ar else f"🌐 Language: {humanize_language(settings.preferred_language, lang)}", callback_data="panel:language"),
            InlineKeyboardButton(f"⚡️ الإرسال: {humanize_delivery_mode(settings.delivery_mode, lang)}" if is_ar else f"⚡️ Delivery: {humanize_delivery_mode(settings.delivery_mode, lang)}", callback_data="panel:delivery"),
        ],
        [
            InlineKeyboardButton(f"🔗 المشاركة: {humanize_share_mode(settings.share_mode, lang)}" if is_ar else f"🔗 Share: {humanize_share_mode(settings.share_mode, lang)}", callback_data="panel:share"),
            InlineKeyboardButton(f"🔢 العدد: {settings.ai_count}" if is_ar else f"🔢 Count: {settings.ai_count}", callback_data="panel:count"),
        ],
        [
            InlineKeyboardButton("🏢 المزود والموديل" if is_ar else "🏢 Provider & Model", callback_data="panel:providers"),
            InlineKeyboardButton("🦙 الموديلات المجانية" if is_ar else "🦙 Free Models", callback_data="panel:freemodels"),
        ],
        [
            InlineKeyboardButton("🛠 أداة الذكاء الحالية" if is_ar else "🛠 Active AI Tool", callback_data="panel:tools"),
            InlineKeyboardButton("🎉 الفواصل الترفيهية" if is_ar else "🎉 Fun Breaks", callback_data="panel:fun"),
        ],
        [
            InlineKeyboardButton(f"🎯 مستوى الصعوبة: {humanize_difficulty(getattr(settings, 'difficulty', 'medium'), lang)}" if is_ar else f"🎯 Difficulty: {humanize_difficulty(getattr(settings, 'difficulty', 'medium'), lang)}", callback_data="panel:difficulty"),
        ],
        [
            InlineKeyboardButton("« الرئيسية" if is_ar else "« Home", callback_data="home"),
        ],
    ])


def build_language_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = settings.preferred_language
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_selected_label("تلقائي 🔄" if lang == "ar" else "Auto 🔄", cur == "auto"), callback_data="set:language:auto"),
            InlineKeyboardButton(_selected_label("العربية 🇸🇦", cur == "ar"), callback_data="set:language:ar"),
            InlineKeyboardButton(_selected_label("English 🇬🇧", cur == "en"), callback_data="set:language:en"),
        ],
        [InlineKeyboardButton("« رجوع" if lang == "ar" else "« Back", callback_data="panel:settings")],
    ])


def build_delivery_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = settings.delivery_mode
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_selected_label("كامل (أزرار وتأكيد) ✨" if lang == "ar" else "Rich (buttons + confirm) ✨", cur == "rich"), callback_data="set:delivery:rich"),
            InlineKeyboardButton(_selected_label("سريع ومباشر ⚡️" if lang == "ar" else "Fast & direct ⚡️", cur == "fast"), callback_data="set:delivery:fast"),
        ],
        [InlineKeyboardButton("« رجوع" if lang == "ar" else "« Back", callback_data="panel:settings")],
    ])


def build_share_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = settings.share_mode
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(_selected_label("تيليجرام فقط ✈️" if lang == "ar" else "Telegram only ✈️", cur == "telegram"), callback_data="set:share:telegram"),
            InlineKeyboardButton(_selected_label("الويب فقط 🌐" if lang == "ar" else "Web only 🌐", cur == "web"), callback_data="set:share:web"),
        ],
        [
            InlineKeyboardButton(_selected_label("تيليجرام والويب معاً 🔗" if lang == "ar" else "Telegram & Web both 🔗", cur == "both"), callback_data="set:share:both"),
        ],
        [InlineKeyboardButton("« رجوع" if lang == "ar" else "« Back", callback_data="panel:settings")],
    ])


def build_count_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = settings.ai_count
    rows = []
    current_row = []
    for count in range(1, 11):
        label = _selected_label(str(count), cur == count)
        current_row.append(InlineKeyboardButton(label, callback_data=f"set:count:{count}"))
        if len(current_row) == 5:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([InlineKeyboardButton("« رجوع" if lang == "ar" else "« Back", callback_data="panel:settings")])
    return InlineKeyboardMarkup(rows)


def build_provider_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = normalize_ai_provider(settings.ai_provider)
    rows = []
    current_row = []
    for code, meta in list(AI_PROVIDER_CATALOG.items())[:12]:
        label = _selected_label(meta.get("ar" if lang == "ar" else "en", code), cur == code)
        current_row.append(InlineKeyboardButton(label, callback_data=f"set:provider:{code}"))
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([InlineKeyboardButton("✨ تفعيل الخيار المجاني الأفضل" if lang == "ar" else "✨ Best Free Profile", callback_data="freeai")])
    rows.append([InlineKeyboardButton("« رجوع" if lang == "ar" else "« Back", callback_data="panel:settings")])
    return InlineKeyboardMarkup(rows)


def build_free_models_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = normalize_free_model(settings.ai_model)
    rows = []
    for code, meta in FREE_MODEL_CATALOG.items():
        title = meta.get("ar" if lang == "ar" else "en", code)
        star = " 🌟" if meta.get("recommended") else ""
        label = _selected_label(f"{title}{star}", cur == code)
        rows.append([InlineKeyboardButton(label, callback_data=f"set:freemodel:{code}")])
    rows.append([InlineKeyboardButton("« رجوع" if lang == "ar" else "« Back", callback_data="panel:settings")])
    return InlineKeyboardMarkup(rows)


def build_tools_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = normalize_ai_tool(settings.ai_tool_mode)
    rows = []
    current_row = []
    for code, meta in list(AI_TOOL_CATALOG.items())[:16]:
        label = _selected_label(meta.get("ar" if lang == "ar" else "en", code), cur == code)
        current_row.append(InlineKeyboardButton(label, callback_data=f"set:tool:{code}"))
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([InlineKeyboardButton("« رجوع" if lang == "ar" else "« Back", callback_data="panel:settings")])
    return InlineKeyboardMarkup(rows)


def build_study_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur = normalize_ai_tool(settings.ai_tool_mode)
    study_keys = ["studypack", "summary", "keypoints", "flashcards", "glossary", "truefalse", "shortanswer", "quiz"]
    rows = []
    current_row = []
    for code in study_keys:
        meta = AI_TOOL_CATALOG.get(code, {})
        label = _selected_label(meta.get("ar" if lang == "ar" else "en", code), cur == code)
        current_row.append(InlineKeyboardButton(label, callback_data=f"set:studytool:{code}"))
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    rows.append([InlineKeyboardButton("« الرئيسية" if lang == "ar" else "« Home", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def build_fun_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    cur_style = normalize_fun_style(settings.fun_style)
    is_ar = lang == "ar"
    rows = [
        [
            InlineKeyboardButton(
                f"🎉 تفعيل الفواصل: {humanize_toggle(settings.fun_breaks, lang)}" if is_ar else f"🎉 Breaks: {humanize_toggle(settings.fun_breaks, lang)}",
                callback_data="toggle:fun",
            )
        ],
        [
            InlineKeyboardButton(_selected_label("منوع 🎲" if is_ar else "Mixed 🎲", cur_style == "mixed"), callback_data="set:funstyle:mixed"),
            InlineKeyboardButton(_selected_label("معلومات 💡" if is_ar else "Trivia 💡", cur_style == "trivia"), callback_data="set:funstyle:trivia"),
            InlineKeyboardButton(_selected_label("ألغاز 🧩" if is_ar else "Riddles 🧩", cur_style == "riddle"), callback_data="set:funstyle:riddle"),
        ],
        [
            InlineKeyboardButton(_selected_label("نكت 😄" if is_ar else "Jokes 😄", cur_style == "joke"), callback_data="set:funstyle:joke"),
            InlineKeyboardButton(_selected_label("كسر الجليد 🧊" if is_ar else "Icebreaker 🧊", cur_style == "icebreaker"), callback_data="set:funstyle:icebreaker"),
            InlineKeyboardButton(_selected_label("نصائح 📚" if is_ar else "Study 📚", cur_style == "study"), callback_data="set:funstyle:study"),
        ],
        [
            InlineKeyboardButton("كل 4 ⏱" if is_ar else "Every 4", callback_data="set:funrate:4"),
            InlineKeyboardButton("كل 6 ⏱" if is_ar else "Every 6", callback_data="set:funrate:6"),
            InlineKeyboardButton("كل 10 ⏱" if is_ar else "Every 10", callback_data="set:funrate:10"),
            InlineKeyboardButton("كل 15 ⏱" if is_ar else "Every 15", callback_data="set:funrate:15"),
        ],
        [InlineKeyboardButton("« رجوع" if is_ar else "« Back", callback_data="panel:settings")],
    ]
    return InlineKeyboardMarkup(rows)


def build_export_keyboard(lang: str = "ar") -> InlineKeyboardMarkup:
    """Build keyboard for quiz exporting options."""
    is_ar = lang == "ar"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 JSON (نسخة كاملة)" if is_ar else "📦 JSON (Full data)", callback_data="export:json"),
            InlineKeyboardButton("📊 CSV (Excel / جداول)" if is_ar else "📊 CSV (Excel / Sheets)", callback_data="export:csv"),
        ],
        [
            InlineKeyboardButton("🎓 Moodle / Anki (GIFT)" if is_ar else "🎓 GIFT (Moodle / Anki)", callback_data="export:gift"),
            InlineKeyboardButton("📝 ملف نصي (TXT)" if is_ar else "📝 Plain Text (TXT)", callback_data="export:text"),
        ],
        [InlineKeyboardButton("« الرئيسية" if is_ar else "« Home", callback_data="home")],
    ])


def build_ai_status_keyboard(lang: str, settings: UserSettings) -> InlineKeyboardMarkup:
    is_ar = lang == "ar"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 تغيير المزود" if is_ar else "🔄 Change Provider", callback_data="panel:providers"),
            InlineKeyboardButton("🦙 الموديلات المجانية" if is_ar else "🦙 Free Models", callback_data="panel:freemodels"),
        ],
        [
            InlineKeyboardButton("⚡️ تفعيل أفضل خيار مجاني" if is_ar else "⚡️ Auto Free Profile", callback_data="freeai"),
        ],
        [InlineKeyboardButton("« رجوع" if is_ar else "« Back", callback_data="panel:settings")],
    ])


def build_external_share_rows(quiz_id: str, lang: str, share_mode: str, question: str = "") -> List[List[InlineKeyboardButton]]:
    if not ENABLE_WEB_PREVIEW or not PUBLIC_BASE_URL:
        return []
    preview_url = f"{PUBLIC_BASE_URL}/quiz/{quiz_id}"
    safe_text = quote_plus(question[:120]) if question else ""
    telegram_share = f"https://t.me/share/url?url={quote_plus(preview_url)}&text={safe_text}"
    whatsapp_share = f"https://wa.me/?text={quote_plus(f'{question[:100]}... {preview_url}') if question else quote_plus(preview_url)}"

    rows = []
    if share_mode in {"web", "both"}:
        rows.append([InlineKeyboardButton(get_text("open_preview", lang), url=preview_url)])
    if share_mode in {"telegram", "both"}:
        rows.append([
            InlineKeyboardButton(get_text("share_quiz", lang), url=telegram_share),
            InlineKeyboardButton("WhatsApp 💬", url=whatsapp_share),
        ])
    return rows


async def build_quiz_keyboard(
    context,
    quiz_id: str,
    lang: str = "ar",
    include_explanation: bool = True,
    share_mode: str = "both",
    question: str = "",
) -> InlineKeyboardMarkup:
    """Build action buttons attached to a published quiz poll."""
    rows = []
    action_row = []
    if include_explanation:
        action_row.append(InlineKeyboardButton(get_text("show_explanation", lang), callback_data=f"explain:{quiz_id}"))
    action_row.append(InlineKeyboardButton(get_text("repost_quiz", lang), callback_data=f"repost:{quiz_id}"))
    if action_row:
        rows.append(action_row)

    external_rows = build_external_share_rows(quiz_id, lang, share_mode, question)
    rows.extend(external_rows)
    return InlineKeyboardMarkup(rows)


def build_exam_question_keyboard(options: List[str], current_idx: int, total_count: int, lang: str = "ar") -> InlineKeyboardMarkup:
    """Build option buttons for interactive exam session."""
    rows = []
    for opt_idx, opt_text in enumerate(options):
        label = chr(65 + opt_idx)
        display = f"{label}) {opt_text[:35]}"
        rows.append([InlineKeyboardButton(display, callback_data=f"exam_ans:{current_idx}:{opt_idx}")])
    
    cancel_label = "❌ إنهاء الاختبار" if lang == "ar" else "❌ Quit Exam"
    rows.append([InlineKeyboardButton(cancel_label, callback_data="exam_quit")])
    return InlineKeyboardMarkup(rows)
