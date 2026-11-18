import unittest
from bot.services.wizard_service import (
    start_wizard,
    get_wizard,
    cancel_wizard,
    QuestionWizard,
)

class TestWizardService(unittest.TestCase):
    def setUp(self):
        cancel_wizard(999)

    def tearDown(self):
        cancel_wizard(999)

    def test_start_and_get_wizard(self):
        wiz = start_wizard(user_id=999, chat_id=888, lang="ar")
        self.assertIsNotNone(wiz)
        self.assertEqual(wiz.user_id, 999)
        self.assertEqual(wiz.chat_id, 888)
        self.assertEqual(wiz.step, "question")

        fetched = get_wizard(999)
        self.assertEqual(fetched, wiz)

    def test_wizard_steps_progression(self):
        wiz = start_wizard(user_id=999, chat_id=888, lang="ar")
        wiz.question = "ما هي عاصمة مصر؟"
        wiz.step = "options"
        wiz.options = ["القاهرة", "الإسكندرية", "أسوان"]
        wiz.step = "correct"
        wiz.correct_option = 0
        wiz.step = "explanation"
        wiz.explanation = "القاهرة هي العاصمة الإدارية والتاريخية."

        self.assertEqual(len(wiz.options), 3)
        self.assertEqual(wiz.correct_option, 0)
        self.assertTrue(wiz.explanation.startswith("القاهرة"))

    def test_cancel_wizard(self):
        start_wizard(user_id=999, chat_id=888, lang="ar")
        cancel_wizard(999)
        self.assertIsNone(get_wizard(999))

if __name__ == "__main__":
    unittest.main()
