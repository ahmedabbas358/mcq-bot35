"""
Unit tests for Interactive Exam / Test Session service.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from bot.services.exam_service import (
    start_exam,
    get_exam,
    finish_exam,
)


class TestExamService(unittest.TestCase):

    def setUp(self):
        self.sample_quizzes = [
            {
                "quiz_id": "q1",
                "question": "Q1?",
                "options": ["A", "B", "C", "D"],
                "correct_option": 0,
            },
            {
                "quiz_id": "q2",
                "question": "Q2?",
                "options": ["A", "B", "C", "D"],
                "correct_option": 1,
            },
        ]

    def test_exam_lifecycle(self):
        user_id = 12345
        chat_id = 67890

        # Start exam
        session = start_exam(user_id, chat_id, self.sample_quizzes, lang="ar")
        self.assertIsNotNone(session)
        self.assertEqual(session.current_index, 0)
        self.assertEqual(session.correct_count, 0)

        # Retrieve active exam
        active = get_exam(user_id, chat_id)
        self.assertIs(active, session)

        # Answer question 1 correctly
        session.correct_count += 1
        session.current_index += 1

        # Finish exam
        summary = finish_exam(user_id, chat_id)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["correct"], 1)
        self.assertEqual(summary["wrong"], 1)
        self.assertEqual(summary["score"], 50)
        self.assertIn("feedback", summary)

        # Exam should no longer be active
        self.assertIsNone(get_exam(user_id, chat_id))


if __name__ == "__main__":
    unittest.main()
