"""
Unit tests for localization and internationalization.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from bot.i18n.texts import TEXTS, get_text


class TestI18n(unittest.TestCase):

    def test_texts_have_translations(self):
        for key, pack in TEXTS.items():
            self.assertIn("ar", pack, f"Key '{key}' is missing Arabic translation")
            self.assertIn("en", pack, f"Key '{key}' is missing English translation")
            self.assertTrue(bool(pack["ar"]), f"Key '{key}' has empty Arabic translation")
            self.assertTrue(bool(pack["en"]), f"Key '{key}' has empty English translation")

    def test_get_text_formatting(self):
        text_ar = get_text("batch_success", "ar", count=5)
        self.assertIn("5", text_ar)
        text_en = get_text("batch_success", "en", count=5)
        self.assertIn("5", text_en)

    def test_fallback_on_missing_key(self):
        missing = get_text("non_existent_key_12345", "ar")
        self.assertEqual(missing, "non_existent_key_12345")


if __name__ == "__main__":
    unittest.main()
