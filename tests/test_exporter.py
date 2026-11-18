"""
Unit tests for Quiz Exporters: JSON, CSV, GIFT, and Text.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import json
import unittest
from bot.services.exporter import (
    create_export_file,
    export_to_csv,
    export_to_gift,
    export_to_json,
    export_to_text,
)


class TestExporter(unittest.TestCase):

    def setUp(self):
        self.sample_quizzes = [
            {
                "quiz_id": "q1",
                "question": "What is Python?",
                "options": ["A snake", "A programming language", "A coffee", "A car"],
                "correct_option": 1,
                "explanation": "Python is a high-level general-purpose programming language.",
            },
            {
                "quiz_id": "q2",
                "question": "ما هي وحدة قياس القوة؟",
                "options": ["الجول", "النيوتن", "الواط", "الفولت"],
                "correct_option": 1,
                "explanation": "يقاس مقدار القوة بوحدة النيوتن تكريماً للعالم إسحاق نيوتن.",
            },
        ]

    def test_export_to_json(self):
        data = export_to_json(self.sample_quizzes)
        parsed = json.loads(data.decode("utf-8"))
        self.assertEqual(parsed["count"], 2)
        self.assertEqual(parsed["quizzes"][0]["question"], "What is Python?")

    def test_export_to_csv(self):
        data = export_to_csv(self.sample_quizzes)
        text = data.decode("utf-8-sig")
        self.assertIn("Question,Option A", text)
        self.assertIn("What is Python?", text)
        self.assertIn("النيوتن", text)

    def test_export_to_gift(self):
        data = export_to_gift(self.sample_quizzes)
        text = data.decode("utf-8")
        self.assertIn("::Q1:: What is Python?", text)
        self.assertIn("=A programming language", text)
        self.assertIn("~A snake", text)
        self.assertIn("::Q2::", text)

    def test_export_to_text(self):
        data = export_to_text(self.sample_quizzes)
        text = data.decode("utf-8")
        self.assertIn("Q1: What is Python?", text)
        self.assertIn("Answer: B", text)
        self.assertIn("Explanation:", text)

    def test_create_export_file(self):
        filename, data = create_export_file(self.sample_quizzes, "csv")
        self.assertTrue(filename.endswith(".csv"))
        self.assertTrue(len(data) > 0)


if __name__ == "__main__":
    unittest.main()
