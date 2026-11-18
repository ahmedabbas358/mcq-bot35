"""
Unit tests for Document and File Parser.
"""

import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import unittest
from bot.parsers.document_parser import extract_text_from_bytes


class TestDocumentParser(unittest.TestCase):

    def test_utf8_decoding(self):
        text = "سؤال: ما هي عاصمة مصر؟\nأ) القاهرة\nب) الإسكندرية\nالإجابة: أ"
        data = text.encode("utf-8")
        extracted = extract_text_from_bytes(data, "test.txt")
        self.assertEqual(extracted, text)

    def test_utf16_decoding(self):
        text = "Q1: What is 2 + 2?\nA) 3\nB) 4\nAnswer: B"
        data = text.encode("utf-16")
        extracted = extract_text_from_bytes(data, "test.txt")
        self.assertEqual(extracted, text)

    def test_empty_bytes(self):
        extracted = extract_text_from_bytes(b"", "empty.txt")
        self.assertIsNone(extracted)


if __name__ == "__main__":
    unittest.main()
