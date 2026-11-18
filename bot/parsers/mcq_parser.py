"""
High-performance MCQ parser with explanation extraction, True/False detection,
and resilient handling of various question and answer formats.
"""

import json
import re
from typing import Dict, List, Optional, Tuple

from bot.config import (
    MAX_MCQ_BLOCK_LINES,
    MAX_OPTION_LENGTH,
    MAX_QUESTION_LENGTH,
)
from bot.constants import (
    ANSWER_KEYWORDS,
    ARABIC_DIGITS,
    ARABIC_LETTERS,
    MCQ_BLOCK_START_RE,
    MCQ_EXPLANATION_RE,
    MCQ_OPTION_PATTERNS,
    MCQ_REFERENCE_ONLY_RE,
    MCQ_UNLABELED_OPTION_PATTERN,
    QUESTION_PREFIXES,
)
from bot.utils.helpers import has_arabic


def get_options_blob(options: List[str]) -> str:
    """Serialize options list to JSON string."""
    return json.dumps(options, ensure_ascii=False)


def parse_options_blob(blob: str) -> List[str]:
    """Deserialize options from JSON or delimiter string."""
    try:
        data = json.loads(blob)
        if isinstance(data, list):
            return [str(item) for item in data]
    except Exception:
        pass
    return [part for part in (blob or "").split(":::") if part]


def validate_mcq(question: str, options: List[str]) -> bool:
    """Validate question and options lengths and counts according to Telegram constraints."""
    if not question or not options:
        return False
    if len(question) > MAX_QUESTION_LENGTH:
        return False
    if len(options) < 2 or len(options) > 10:
        return False
    if any(len(opt) > MAX_OPTION_LENGTH for opt in options):
        return False
    return True


def is_mcq_question_start(line: str) -> bool:
    """Check if line looks like the start of a question."""
    return bool(MCQ_BLOCK_START_RE.match((line or "").strip()))


def is_mcq_option_line(line: str) -> bool:
    """Check if line matches any MCQ option patterns."""
    stripped = (line or "").strip()
    for pattern in MCQ_OPTION_PATTERNS:
        if re.match(pattern, stripped, re.I | re.U):
            return True
    return False


def is_mcq_answer_line(line: str) -> bool:
    """Check if line contains an answer indicator."""
    lowered = (line or "").strip().lower()
    if not lowered:
        return False
    for keyword in ANSWER_KEYWORDS:
        if keyword.lower() in lowered:
            return True
    return False


def is_mcq_explanation_line(line: str) -> bool:
    """Check if line introduces an explanation or reference note."""
    return bool(MCQ_EXPLANATION_RE.match((line or "").strip()))


def extract_explanation_from_lines(lines: List[str]) -> Tuple[List[str], str]:
    """Separate question/options lines from explanation lines."""
    cleaned_lines: List[str] = []
    explanation_parts: List[str] = []
    in_explanation = False

    for raw_line in lines:
        line = (raw_line or "").strip()
        if not line:
            continue
        if is_mcq_explanation_line(line):
            in_explanation = True
            # Extract content after prefix
            content = re.sub(
                r"^\s*(?:Explanation|Exp|Reason|Note|Reference|Source|شرح|الشرح|تفسير|التفسير|توضيح|التوضيح|ملاحظة|مرجع)\s*[:\-]\s*",
                "",
                line,
                flags=re.I,
            ).strip()
            if content:
                explanation_parts.append(content)
            continue
        if MCQ_REFERENCE_ONLY_RE.match(line):
            continue
        if in_explanation:
            if is_mcq_question_start(line):
                in_explanation = False
                cleaned_lines.append(line)
            else:
                explanation_parts.append(line)
        else:
            cleaned_lines.append(line)

    return cleaned_lines, " ".join(explanation_parts).strip()


def looks_like_mcq_batch(text: str) -> bool:
    """Heuristic check to determine if text looks like one or more MCQs."""
    raw = (text or "").strip()
    if not raw:
        return False
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    option_hits = sum(1 for line in lines if is_mcq_option_line(line))
    answer_hits = sum(1 for line in lines if is_mcq_answer_line(line))
    question_hits = sum(1 for line in lines if is_mcq_question_start(line))
    return option_hits >= 2 and (answer_hits >= 1 or question_hits >= 1)


def parse_single_mcq(block: str) -> Optional[Tuple[str, List[str], int, str]]:
    """Parse a single MCQ block into (question, options, correct_index, explanation)."""
    block = re.sub(r"[\u200b\u200c\ufeff]", "", block)
    raw_lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(raw_lines) > MAX_MCQ_BLOCK_LINES:
        return None

    lines, explanation = extract_explanation_from_lines(raw_lines)
    if not lines:
        return None

    question = None
    options: List[Tuple[str, str]] = []
    answer_label = None
    answer_line = ""
    unlabeled_options: List[str] = []
    inline_correct_idx: Optional[int] = None

    question_prefixes = QUESTION_PREFIXES

    for line in lines:
        if question is None:
            question_candidate = re.sub(
                r"^\s*(?:(?:Q(?:uestion)?|MCQ|س(?:ؤال)?)\s*[\d\u0660-\u0669\u06f0-\u06f9]*\s*[\).:\-]?"
                r"|[\[(]?\s*[\d\u0660-\u0669\u06f0-\u06f9]+\s*[\])\.:\-])\s*",
                "",
                line,
                flags=re.I,
            ).strip()
            if question_candidate and question_candidate != line and not is_mcq_option_line(question_candidate):
                question = question_candidate
            else:
                for prefix in question_prefixes:
                    if line.lower().startswith(prefix.lower()):
                        question = re.sub(f"^{re.escape(prefix)}\\s*[:.\\-]?\\s*", "", line, flags=re.I).strip()
                        break
            if question is not None:
                continue

        matched = False
        for pattern in MCQ_OPTION_PATTERNS:
            match = re.match(pattern, line, re.I | re.U)
            if match:
                label, text = match.groups()
                # Check for inline checkmark or star indicating correct answer
                is_marked_correct = bool(re.search(r"[\u2705\u2713\u2714\u2b50\u25cb\u25cf\u2728]|✔️|✅|⭐|\(صحيح\)|\(صح\)", text))
                if is_marked_correct:
                    text = re.sub(r"[\u2705\u2713\u2714\u2b50\u2728]|✔️|✅|⭐|\(صحيح\)|\(صح\)", "", text).strip()
                    inline_correct_idx = len(options)

                # Normalize label
                label = "".join(ARABIC_DIGITS.get(char, char) for char in label).upper()
                label = "".join(ARABIC_LETTERS.get(char, char) for char in label).strip()
                if label:
                    options.append((label, text.strip()))
                    matched = True
                    break
        if matched:
            continue

        unlabeled_match = re.match(MCQ_UNLABELED_OPTION_PATTERN, line, re.U)
        if unlabeled_match:
            u_text = unlabeled_match.group(1).strip()
            if re.search(r"[\u2705\u2713\u2714\u2b50]|✔️|✅|⭐", u_text):
                inline_correct_idx = len(unlabeled_options)
                u_text = re.sub(r"[\u2705\u2713\u2714\u2b50]|✔️|✅|⭐", "", u_text).strip()
            unlabeled_options.append(u_text)
            continue

        if answer_label is None:
            for keyword in ANSWER_KEYWORDS:
                if keyword.lower() in line.lower():
                    answer_line = line.strip()
                    patterns = [
                        r"[:：]\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])$",
                        r"is\s+([a-zأ-ي0-9])",
                        r"هي\s+([a-zأ-ي0-9])",
                        r"[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]$",
                        r"\b(?:correct|صح|صحيح)\s*[:\-]\s*([a-zأ-ي0-9])",
                        r"[\u2714\u2705]\s*([a-zأ-ي0-9])",
                    ]
                    for pat in patterns:
                        match = re.search(pat, line, re.I | re.U)
                        if match:
                            answer_label = match.group(1)
                            answer_label = "".join(ARABIC_DIGITS.get(char, char) for char in answer_label).upper()
                            answer_label = "".join(ARABIC_LETTERS.get(char, char) for char in answer_label).strip()
                            break
                    break

    if not options and 2 <= len(unlabeled_options) <= 10:
        options = [(chr(65 + idx), option) for idx, option in enumerate(unlabeled_options[:10])]

    if question is None and lines:
        question = lines[0]
        if options and lines[0].startswith(options[0][0]):
            question = None

    # True / False fallback
    if question and not options and answer_line:
        lower_answer_line = answer_line.lower()
        if any(token in lower_answer_line for token in ["true", "false", "صح", "خطأ", "صحيح", "غلط"]):
            if has_arabic(question + answer_line):
                options = [("A", "صح"), ("B", "خطأ")]
            else:
                options = [("A", "True"), ("B", "False")]
            if any(token in lower_answer_line for token in ["true", "صح", "صحيح"]):
                answer_label = "A"
            elif any(token in lower_answer_line for token in ["false", "خطأ", "غلط"]):
                answer_label = "B"

    if not question or not options:
        return None

    # Determine correct option index
    if inline_correct_idx is not None and 0 <= inline_correct_idx < len(options):
        return question, [item for _, item in options], inline_correct_idx, explanation

    label_to_idx: Dict[str, int] = {}
    option_text_to_idx: Dict[str, int] = {}
    for idx, (label, option_text) in enumerate(options):
        clean_label = re.sub(r"[^A-Z0-9]", "", label)
        label_to_idx[clean_label] = idx
        if clean_label.isdigit() and 1 <= int(clean_label) <= 26:
            label_to_idx[chr(64 + int(clean_label))] = idx
        option_text_to_idx[re.sub(r"\s+", " ", option_text).strip().lower()] = idx

    if answer_label:
        clean_answer = re.sub(r"[^A-Z0-9]", "", answer_label)
        if clean_answer in label_to_idx:
            return question, [item for _, item in options], label_to_idx[clean_answer], explanation
    else:
        clean_answer = ""

    text_answers = {
        "الأول": "A", "أول": "A", "أ": "A", "1": "A",
        "الثاني": "B", "ثاني": "B", "ب": "B", "2": "B",
        "الثالث": "C", "ثالث": "C", "ت": "C", "3": "C",
        "الرابع": "D", "رابع": "D", "ث": "D", "4": "D",
        "الخامس": "E", "خامس": "E", "ج": "E", "5": "E",
        "first": "A", "1st": "A",
        "second": "B", "2nd": "B",
        "true": "A", "false": "B",
        "صح": "A", "خطأ": "B", "صحيح": "A", "غلط": "B",
    }
    if clean_answer in text_answers and text_answers[clean_answer] in label_to_idx:
        return question, [item for _, item in options], label_to_idx[text_answers[clean_answer]], explanation

    if answer_line:
        normalized_answer_line = re.sub(r"^(?:answer|ans|correct answer|الإجابة|الجواب|الحل|solution)\s*[:\-]?\s*", "", answer_line, flags=re.I).strip().lower()
        normalized_answer_line = re.sub(r"\s+", " ", normalized_answer_line)
        if normalized_answer_line in option_text_to_idx:
            return question, [item for _, item in options], option_text_to_idx[normalized_answer_line], explanation
        for option_text, idx in option_text_to_idx.items():
            if option_text and option_text in normalized_answer_line:
                return question, [item for _, item in options], idx, explanation

    # If question and options are valid but answer is deferred (e.g. bottom answer key)
    if question and options and len(options) >= 2:
        return question, [item for _, item in options], -1, explanation

    return None


def extract_batch_answer_keys(text: str) -> Dict[int, str]:
    """Parse batch answer keys at bottom of text like: 1-A, 2-C or Answers: 1. A, 2. B."""
    keys: Dict[int, str] = {}
    pattern = re.compile(r"(?:^|\s)(?:Q|س)?(\d+)[\s.:\-)_]+([A-Da-dأ-د1-4])", re.M)
    for match in pattern.finditer(text):
        q_num = int(match.group(1))
        ans_raw = match.group(2).upper()
        ans_norm = "".join(ARABIC_LETTERS.get(c, c) for c in ans_raw)
        keys[q_num] = ans_norm
    return keys


def parse_mcq(text: str) -> List[Tuple[str, List[str], int, str]]:
    """Parse entire text into a list of (question, options, correct_index, explanation) tuples."""
    text = (text or "").strip()
    if "|" in text:
        text = re.sub(r"\s*\|\s*", "\n", text)
    text = re.sub(r"(?<!\n)(\s+[A-Da-dأ-د1-9][).:\-]\s+)", lambda m: "\n" + m.group(1).strip() + " ", text)
    text = re.sub(r"(?<!\n)(\s+(?:Answer|Ans|Correct Answer|الإجابة|الجواب)\s*[:\-]\s*)", lambda m: "\n" + m.group(1).strip() + " ", text, flags=re.I)

    # Check for bottom answer key
    bottom_keys = {}
    key_section_match = re.search(r"(?:\n|^)\s*(?:Answers|Answer Key|مفتاح الحل|الإجابات)\s*[:\-]\s*(.+)$", text, re.I | re.S)
    if key_section_match:
        bottom_keys = extract_batch_answer_keys(key_section_match.group(1))
        # Remove answer key section from main text for block parsing
        text = text[:key_section_match.start()].strip()

    blocks: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        if current and is_mcq_question_start(stripped):
            blocks.append("\n".join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        blocks.append("\n".join(current))

    parsed: List[Tuple[str, List[str], int, str]] = []
    for idx, block in enumerate(blocks, start=1):
        item = parse_single_mcq(block)
        if item:
            parsed.append(item)
            continue
        # Try sub-block splitting if multiple questions were glued together
        sub_blocks = re.split(
            r"(?=^\s*(?:(?:Q(?:uestion)?|MCQ|س(?:ؤال)?)\s*[\d\u0660-\u0669\u06f0-\u06f9]*\s*[\).:\-]?"
            r"|[\[(]?\s*[\d\u0660-\u0669\u06f0-\u06f9]+\s*[\])\.:\-]))",
            block,
            flags=re.M | re.I,
        )
        for sub_block in sub_blocks:
            if sub_block.strip():
                sub_item = parse_single_mcq(sub_block)
                if sub_item:
                    parsed.append(sub_item)

    # Apply bottom answer keys if some questions lacked answers
    if bottom_keys:
        refined: List[Tuple[str, List[str], int, str]] = []
        for q_idx, (q, opts, ans_idx, expl) in enumerate(parsed, start=1):
            if ans_idx is None or ans_idx < 0:
                key = bottom_keys.get(q_idx)
                if key:
                    key_idx = ord(key) - 65 if "A" <= key <= "Z" else -1
                    if 0 <= key_idx < len(opts):
                        ans_idx = key_idx
            if ans_idx is not None and ans_idx >= 0:
                refined.append((q, opts, ans_idx, expl))
        parsed = refined
    else:
        parsed = [item for item in parsed if item[2] is not None and item[2] >= 0]

    return parsed
