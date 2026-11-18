"""
Persistent Reply Keyboard builders for Telegram bottom menu bar.
Provides instant, zero-friction access to main features.
"""

try:
    from telegram import KeyboardButton, ReplyKeyboardMarkup
except ImportError:
    class KeyboardButton:
        def __init__(self, text, **kwargs):
            self.text = text
            self.__dict__.update(kwargs)
    class ReplyKeyboardMarkup:
        def __init__(self, keyboard, resize_keyboard=True, **kwargs):
            self.keyboard = keyboard
            self.resize_keyboard = resize_keyboard


def build_reply_main_menu(lang: str = "ar") -> ReplyKeyboardMarkup:
    """Build persistent quick-action keyboard pinned to bottom of Telegram."""
    if lang == "ar":
        return ReplyKeyboardMarkup(
            [
                [KeyboardButton("✍️ إنشاء سؤال جديد"), KeyboardButton("🎯 بدء اختبار تفاعلي")],
                [KeyboardButton("🤖 الذكاء الاصطناعي"), KeyboardButton("🎓 بطاقة إنجازاتي")],
                [KeyboardButton("⚙️ الإعدادات"), KeyboardButton("📋 نموذج التنسيق")],
            ],
            resize_keyboard=True,
            one_time_keyboard=False,
        )
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("✍️ Create Question"), KeyboardButton("🎯 Start Exam")],
            [KeyboardButton("🤖 AI Generator"), KeyboardButton("🎓 My Report Card")],
            [KeyboardButton("⚙️ Settings"), KeyboardButton("📋 Copyable Template")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )
