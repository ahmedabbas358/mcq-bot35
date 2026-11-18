"""
Quiz exporter service to output quizzes in JSON, CSV, Moodle/Anki GIFT format, or formatted Text.
"""

import csv
import io
import json
from typing import Any, Dict, List, Tuple


def export_to_json(quizzes: List[Dict[str, Any]]) -> bytes:
    """Export quizzes to clean indented JSON format."""
    data = {
        "count": len(quizzes),
        "quizzes": quizzes,
    }
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def export_to_csv(quizzes: List[Dict[str, Any]]) -> bytes:
    """Export quizzes to CSV compatible with Microsoft Excel and Google Sheets."""
    output = io.StringIO()
    writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
    # Header
    writer.writerow([
        "Question",
        "Option A",
        "Option B",
        "Option C",
        "Option D",
        "Option E",
        "Correct Option",
        "Explanation",
    ])
    for q in quizzes:
        opts = q.get("options") or []
        opt_a = opts[0] if len(opts) > 0 else ""
        opt_b = opts[1] if len(opts) > 1 else ""
        opt_c = opts[2] if len(opts) > 2 else ""
        opt_d = opts[3] if len(opts) > 3 else ""
        opt_e = opts[4] if len(opts) > 4 else ""
        correct_idx = q.get("correct_option", 0)
        correct_letter = chr(65 + correct_idx) if 0 <= correct_idx < len(opts) else str(correct_idx)
        writer.writerow([
            q.get("question", ""),
            opt_a,
            opt_b,
            opt_c,
            opt_d,
            opt_e,
            correct_letter,
            q.get("explanation", ""),
        ])
    return output.getvalue().encode("utf-8-sig")  # UTF-8 with BOM for Excel compatibility


def export_to_gift(quizzes: List[Dict[str, Any]]) -> bytes:
    """Export quizzes to GIFT format for Moodle and Anki flashcards."""
    lines = []
    for idx, q in enumerate(quizzes, start=1):
        question = (q.get("question") or "").strip().replace("~", "\\~").replace("=", "\\=").replace("{", "\\{").replace("}", "\\}")
        opts = q.get("options") or []
        correct_idx = q.get("correct_option", 0)
        explanation = (q.get("explanation") or "").strip()
        expl_suffix = f" #{explanation}" if explanation else ""

        gift_options = []
        for opt_idx, opt in enumerate(opts):
            clean_opt = str(opt).strip().replace("~", "\\~").replace("=", "\\=").replace("{", "\\{").replace("}", "\\}")
            if opt_idx == correct_idx:
                gift_options.append(f"={clean_opt}{expl_suffix}")
            else:
                gift_options.append(f"~{clean_opt}")

        body = " ".join(gift_options)
        lines.append(f"::Q{idx}:: {question} {{\n  {body}\n}}\n")

    return "\n".join(lines).encode("utf-8")


def export_to_text(quizzes: List[Dict[str, Any]]) -> bytes:
    """Export quizzes to formatted plain text."""
    lines = []
    for idx, q in enumerate(quizzes, start=1):
        lines.append(f"Q{idx}: {q.get('question', '')}")
        opts = q.get("options") or []
        for opt_idx, opt in enumerate(opts):
            label = chr(65 + opt_idx)
            lines.append(f"  {label}) {opt}")
        correct_idx = q.get("correct_option", 0)
        correct_label = chr(65 + correct_idx) if 0 <= correct_idx < len(opts) else str(correct_idx)
        lines.append(f"Answer: {correct_label}")
        if q.get("explanation"):
            lines.append(f"Explanation: {q['explanation']}")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def create_export_file(quizzes: List[Dict[str, Any]], format_type: str = "json") -> Tuple[str, bytes]:
    """Generate filename and byte payload based on chosen format."""
    fmt = format_type.strip().lower()
    if fmt == "csv":
        return "quizzes_export.csv", export_to_csv(quizzes)
    if fmt in {"gift", "moodle", "anki"}:
        return "quizzes_export.gift.txt", export_to_gift(quizzes)
    if fmt in {"text", "txt"}:
        return "quizzes_export.txt", export_to_text(quizzes)
    return "quizzes_export.json", export_to_json(quizzes)
