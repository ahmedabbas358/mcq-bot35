"""
Unit tests for Offline AI fallback engine: question generation, study packs, terms extraction.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from bot.services.offline_ai import (
    extract_key_terms,
    local_quiz_pack,
    local_study_pack,
    local_fun_break_message,
)


class TestOfflineAI(unittest.TestCase):

    def test_extract_key_terms(self):
        text = "Photosynthesis is the biological process used by plants to convert light energy into chemical energy."
        terms = extract_key_terms(text, limit=4)
        self.assertTrue(len(terms) > 0)
        # Should not include stopwords like 'is', 'the', 'by', 'into'
        for t in terms:
            self.assertNotIn(t.lower(), {"the", "is", "by", "into"})

    def test_local_quiz_pack_topic(self):
        topic = "Machine Learning and Neural Networks"
        quizzes = local_quiz_pack(topic, lang="ar", count=3, mode="topic")
        self.assertEqual(len(quizzes), 3)
        for q, opts, ans_idx, expl in quizzes:
            self.assertTrue(bool(q))
            self.assertEqual(len(opts), 4)
            self.assertTrue(0 <= ans_idx < 4)
            self.assertTrue(bool(expl))

    def test_local_quiz_pack_text(self):
        text = "Water freezes at 0 degrees Celsius and boils at 100 degrees Celsius under normal atmospheric pressure."
        quizzes = local_quiz_pack(text, lang="en", count=2, mode="quizify")
        self.assertEqual(len(quizzes), 2)
        for q, opts, ans_idx, expl in quizzes:
            self.assertTrue(bool(q))
            self.assertEqual(len(opts), 4)
            self.assertTrue(0 <= ans_idx < 4)

    def test_local_study_pack(self):
        text = "الذكاء الاصطناعي هو سلوك وخصائص معينة تتسم بها البرامج الحاسوبية تجعلها تحاكي القدرات الذهنية البشرية."
        pack = local_study_pack(text, lang="ar")
        self.assertIn("الملخص", pack)
        self.assertIn("النقاط المهمة", pack)
        self.assertIn("بطاقات", pack)

    def test_local_fun_break(self):
        joke_ar = local_fun_break_message("joke", "ar")
        self.assertTrue(bool(joke_ar))
        trivia_en = local_fun_break_message("trivia", "en")
        self.assertTrue(bool(trivia_en))


if __name__ == "__main__":
    unittest.main()
