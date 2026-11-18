"""
Unit tests for MCQ Parser: formats, explanations, answer detection, True/False, and batch keys.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from bot.parsers.mcq_parser import (
    parse_single_mcq,
    parse_mcq,
    looks_like_mcq_batch,
    validate_mcq,
)


class TestMCQParser(unittest.TestCase):

    def test_basic_english_mcq(self):
        text = """
        Q1. What is the capital of France?
        A) Berlin
        B) Madrid
        C) Paris
        D) Rome
        Answer: C
        Explanation: Paris is the capital and largest city of France.
        """
        results = parse_mcq(text)
        self.assertEqual(len(results), 1)
        q, opts, ans_idx, expl = results[0]
        self.assertIn("capital of France", q)
        self.assertEqual(len(opts), 4)
        self.assertEqual(opts[ans_idx], "Paris")
        self.assertEqual(ans_idx, 2)
        self.assertIn("largest city of France", expl)

    def test_basic_arabic_mcq(self):
        text = """
        س1: ما هي عاصمة المملكة العربية السعودية؟
        أ) الرياض
        ب) جدة
        ج) مكة المكرمة
        د) المدينة المنورة
        الإجابة: أ
        الشرح: الرياض هي العاصمة الرسمية وأكبر مدن المملكة.
        """
        results = parse_mcq(text)
        self.assertEqual(len(results), 1)
        q, opts, ans_idx, expl = results[0]
        self.assertIn("عاصمة المملكة", q)
        self.assertEqual(len(opts), 4)
        self.assertEqual(opts[ans_idx], "الرياض")
        self.assertEqual(ans_idx, 0)
        self.assertIn("الرياض هي العاصمة", expl)

    def test_inline_checkmark_detection(self):
        text = """
        ما هي عملة اليابان؟
        A) الدولار
        B) الين ✅
        C) اليورو
        D) الجنيه
        """
        results = parse_mcq(text)
        self.assertEqual(len(results), 1)
        q, opts, ans_idx, expl = results[0]
        self.assertEqual(ans_idx, 1)
        self.assertEqual(opts[ans_idx], "الين")

    def test_true_false_arabic(self):
        text = """
        سؤال: تشرق الشمس من الشرق دائماً.
        الجواب: صح
        """
        results = parse_mcq(text)
        self.assertEqual(len(results), 1)
        q, opts, ans_idx, expl = results[0]
        self.assertEqual(opts, ["صح", "خطأ"])
        self.assertEqual(ans_idx, 0)

    def test_true_false_english(self):
        text = """
        Question: The Earth is flat.
        Answer: False
        """
        results = parse_mcq(text)
        self.assertEqual(len(results), 1)
        q, opts, ans_idx, expl = results[0]
        self.assertEqual(opts, ["True", "False"])
        self.assertEqual(ans_idx, 1)

    def test_unlabeled_bullets(self):
        text = """
        Which gas is most abundant in the atmosphere?
        - Oxygen
        - Nitrogen ✅
        - Carbon dioxide
        - Argon
        """
        results = parse_mcq(text)
        self.assertEqual(len(results), 1)
        q, opts, ans_idx, expl = results[0]
        self.assertEqual(len(opts), 4)
        self.assertEqual(opts[ans_idx], "Nitrogen")
        self.assertEqual(ans_idx, 1)

    def test_bottom_answer_key(self):
        text = """
        1. First question
        A) Opt 1
        B) Opt 2
        
        2. Second question
        A) Opt A
        B) Opt B
        C) Opt C
        
        Answers:
        1 - A
        2 - C
        """
        results = parse_mcq(text)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0][2], 0)
        self.assertEqual(results[1][2], 2)

    def test_looks_like_mcq_batch(self):
        batch = "Q1. Hello?\nA) Yes\nB) No\nAnswer: A"
        self.assertTrue(looks_like_mcq_batch(batch))
        
        not_batch = "Hello, how are you today? Let's study!"
        self.assertFalse(looks_like_mcq_batch(not_batch))

    def test_validate_mcq(self):
        self.assertTrue(validate_mcq("Valid question?", ["Opt 1", "Opt 2"]))
        self.assertFalse(validate_mcq("", ["Opt 1", "Opt 2"]))
        self.assertFalse(validate_mcq("Valid?", ["Opt 1"]))  # Too few options
        self.assertFalse(validate_mcq("Valid?", ["Opt"] * 11))  # Too many options


if __name__ == "__main__":
    unittest.main()
