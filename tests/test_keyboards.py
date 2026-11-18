import unittest
from bot.config import UserSettings
from bot.keyboards.inline import (
    build_main_keyboard,
    build_difficulty_keyboard,
    build_controls_keyboard,
    build_export_keyboard,
)

class TestKeyboards(unittest.TestCase):
    def test_build_main_keyboard_ar(self):
        kb = build_main_keyboard("ar")
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertTrue(any("اختبار" in b for b in buttons))
        self.assertTrue(any("لوحة" in b for b in buttons))

    def test_build_main_keyboard_en(self):
        kb = build_main_keyboard("en")
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertTrue(any("Start Exam" in b for b in buttons))
        self.assertTrue(any("Leaderboard" in b for b in buttons))

    def test_build_difficulty_keyboard(self):
        settings = UserSettings(difficulty="medium")
        kb = build_difficulty_keyboard("ar", settings)
        buttons = [btn.text for row in kb.inline_keyboard for btn in row]
        self.assertTrue(any("متوسط" in b for b in buttons))

    def test_build_export_keyboard(self):
        kb = build_export_keyboard("ar")
        data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        self.assertIn("export:json", data)
        self.assertIn("export:csv", data)
        self.assertIn("export:gift", data)
        self.assertIn("export:text", data)

if __name__ == "__main__":
    unittest.main()
