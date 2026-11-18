"""
MCQ Bot 35 - Unified CLI & Management Tool
Provides command-line actions for testing, diagnostics, database management, and local quiz export.
"""

import argparse
import asyncio
import os
import sqlite3
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from bot.config import DB_PATH, OPENAI_MODEL
from bot.services.exporter import create_export_file


def cmd_run(args: argparse.Namespace) -> None:
    """Run the Telegram Bot application."""
    print("🚀 Starting MCQ Bot 35...")
    from bot.app import main
    main()


def cmd_test(args: argparse.Namespace) -> None:
    """Run the full unit test suite."""
    print("🧪 Running unit tests...")
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT_DIR / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


def cmd_check(args: argparse.Namespace) -> None:
    """Run system, environment, and connectivity diagnostics."""
    print("🔍 MCQ Bot 35 System Health Diagnostics")
    print("=" * 45)

    # 1. Environment variables
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
    if token:
        masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
        print(f"  [✓] TELEGRAM_BOT_TOKEN: configured ({masked})")
    else:
        print("  [✗] TELEGRAM_BOT_TOKEN: NOT SET! Please add to .env file.")

    # 2. Database check
    db_file = Path(DB_PATH)
    if db_file.exists():
        size_kb = round(db_file.stat().st_size / 1024, 2)
        print(f"  [✓] Database File: found '{DB_PATH}' ({size_kb} KB)")
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cur.fetchall()]
            print(f"      Tables ({len(tables)}): {', '.join(tables)}")
            conn.close()
        except Exception as e:
            print(f"  [!] Database Connection Warning: {e}")
    else:
        print(f"  [i] Database File: '{DB_PATH}' does not exist yet (will be created automatically on first run).")

    # 3. AI Providers check
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    print(f"  [{'✓' if openai_key else 'i'}] OpenAI API Key: {'Present' if openai_key else 'None (Offline fallback ready)'}")
    print(f"  [{'✓' if gemini_key else 'i'}] Gemini API Key: {'Present' if gemini_key else 'None'}")
    print(f"  [✓] Default Model: {OPENAI_MODEL}")

    # 4. Optional dependencies
    deps = [
        ("telegram", "python-telegram-bot"),
        ("aiosqlite", "aiosqlite"),
        ("flask", "Flask"),
        ("psutil", "psutil (optional)"),
        ("langdetect", "langdetect (optional)"),
    ]
    print("\n📦 Python Package Status:")
    for mod_name, pkg_name in deps:
        try:
            __import__(mod_name)
            print(f"  [✓] {pkg_name}: installed")
        except ImportError:
            print(f"  [-] {pkg_name}: not installed (run 'pip install -r requirements.txt')")

    print("\n✅ Diagnostics completed.")


def cmd_stats(args: argparse.Namespace) -> None:
    """Print database statistics."""
    if not Path(DB_PATH).exists():
        print(f"Database file '{DB_PATH}' not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        total_quizzes = cur.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0]
    except Exception:
        total_quizzes = 0

    try:
        total_users = cur.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0]
    except Exception:
        total_users = 0

    try:
        total_exams = cur.execute("SELECT COUNT(*) FROM exam_results").fetchone()[0]
    except Exception:
        total_exams = 0

    try:
        top_exams = cur.execute("SELECT user_name, percentage, duration FROM exam_results ORDER BY percentage DESC LIMIT 3").fetchall()
    except Exception:
        top_exams = []

    print("\n📊 Database Summary:")
    print(f"  • Stored Quizzes: {total_quizzes}")
    print(f"  • Registered Users: {total_users}")
    print(f"  • Exams Completed: {total_exams}")

    if top_exams:
        print("\n🏆 Top Exam Performers:")
        for idx, row in enumerate(top_exams, start=1):
            print(f"  {idx}. {row['user_name']} - {row['percentage']}% ({row['duration']}s)")

    conn.close()


def cmd_export(args: argparse.Namespace) -> None:
    """Export quizzes directly from database to a local file."""
    if not Path(DB_PATH).exists():
        print(f"Error: Database file '{DB_PATH}' not found.")
        return

    import json
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        rows = cur.execute("SELECT quiz_id, question, options, correct_option, explanation, created_at FROM quizzes ORDER BY created_at DESC").fetchall()
    except Exception as e:
        print(f"Error reading quizzes: {e}")
        conn.close()
        return

    if not rows:
        print("No quizzes found in database to export.")
        conn.close()
        return

    quizzes = []
    for r in rows:
        try:
            opts = json.loads(r["options"] or "[]")
        except Exception:
            opts = [p for p in (r["options"] or "").split(":::") if p]
        quizzes.append({
            "quiz_id": r["quiz_id"],
            "question": r["question"],
            "options": opts,
            "correct_option": r["correct_option"],
            "explanation": r["explanation"] or "",
        })

    fmt = args.format.lower()
    default_filename, data_bytes = create_export_file(quizzes, fmt)
    output_path = Path(args.output) if args.output else ROOT_DIR / default_filename

    with open(output_path, "wb") as f:
        f.write(data_bytes)

    print(f"✅ Exported {len(quizzes)} quizzes to: {output_path.resolve()}")
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MCQ Bot 35 Management CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # run
    subparsers.add_parser("run", help="Run the Telegram Bot")

    # test
    test_p = subparsers.add_parser("test", help="Run all unit tests")
    test_p.add_argument("-v", "--verbose", action="store_true", help="Verbose test output")

    # check
    subparsers.add_parser("check", help="Run diagnostics check on environment and dependencies")

    # stats
    subparsers.add_parser("stats", help="Display summary database statistics")

    # export
    export_p = subparsers.add_parser("export", help="Export quizzes to CSV, JSON, GIFT, or TXT")
    export_p.add_argument("-f", "--format", choices=["csv", "json", "gift", "text", "txt"], default="csv", help="Export format (default: csv)")
    export_p.add_argument("-o", "--output", help="Output file path (optional)")

    args = parser.parse_args()
    if args.command == "run":
        cmd_run(args)
    elif args.command == "test":
        cmd_test(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "export":
        cmd_export(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
