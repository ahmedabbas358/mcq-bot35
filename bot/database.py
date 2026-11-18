"""
Database layer using async SQLite (aiosqlite) with connection management and schema migrations.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import aiosqlite
except ImportError:
    aiosqlite = None

from bot.config import (
    AI_DEFAULT_COUNT,
    DB_PATH,
    DEFAULT_DELETE_SOURCE,
    OPENAI_MODEL,
    QUIZ_CONFIRMATION_MESSAGE,
    Target,
    UserSettings,
)
from bot.utils.helpers import (
    deserialize_target,
    infer_lang,
    serialize_target,
)

logger = logging.getLogger(__name__)


class DB:
    """Async database connection singleton."""
    _pool: Optional[aiosqlite.Connection] = None

    @classmethod
    async def conn(cls) -> aiosqlite.Connection:
        if cls._pool is None:
            cls._pool = await aiosqlite.connect(DB_PATH)
            cls._pool.row_factory = aiosqlite.Row
            # Enable WAL mode for high concurrency & reliability
            await cls._pool.execute("PRAGMA journal_mode = WAL")
            await cls._pool.execute("PRAGMA synchronous = NORMAL")
        return cls._pool

    @classmethod
    async def close(cls) -> None:
        if cls._pool is not None:
            try:
                await cls._pool.close()
            except Exception as exc:
                logger.warning("Error closing DB: %s", exc)
            finally:
                cls._pool = None


async def ensure_column(conn: aiosqlite.Connection, table: str, column: str, definition: str) -> None:
    """Ensure a column exists on a table, adding it dynamically if missing."""
    rows = await (await conn.execute(f"PRAGMA table_info({table})")).fetchall()
    existing = {row["name"] for row in rows}
    if column not in existing:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def init_db() -> None:
    """Initialize database tables and run schema migrations."""
    conn = await DB.conn()
    await conn.execute("CREATE TABLE IF NOT EXISTS user_stats(user_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS channel_stats(chat_id INTEGER PRIMARY KEY, sent INTEGER DEFAULT 0)")
    await conn.execute("CREATE TABLE IF NOT EXISTS known_channels(chat_id INTEGER PRIMARY KEY, title TEXT)")
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS quizzes("
        "quiz_id TEXT PRIMARY KEY, "
        "question TEXT, "
        "options TEXT, "
        "correct_option INTEGER, "
        "user_id INTEGER)"
    )
    await conn.execute("CREATE TABLE IF NOT EXISTS default_channels(user_id INTEGER PRIMARY KEY, chat_id INTEGER, title TEXT)")
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS user_settings("
        "user_id INTEGER PRIMARY KEY, "
        "default_target TEXT, "
        "default_target_title TEXT DEFAULT '', "
        "delete_source INTEGER DEFAULT 0, "
        "ai_enabled INTEGER DEFAULT 1, "
        "ai_model TEXT DEFAULT '', "
        "ai_provider TEXT DEFAULT 'auto', "
        "ai_count INTEGER DEFAULT 3, "
        "preferred_language TEXT DEFAULT 'auto', "
        "ai_specialty TEXT DEFAULT '', "
        "delivery_mode TEXT DEFAULT 'rich', "
        "share_mode TEXT DEFAULT 'both', "
        "show_explanation INTEGER DEFAULT 1, "
        "confirmation_message INTEGER DEFAULT 1, "
        "ai_tool_mode TEXT DEFAULT 'quiz', "
        "fun_breaks INTEGER DEFAULT 0, "
        "fun_interval INTEGER DEFAULT 6, "
        "fun_style TEXT DEFAULT 'mixed')"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS target_stats("
        "target_id TEXT PRIMARY KEY, "
        "chat_type TEXT DEFAULT '', "
        "title TEXT DEFAULT '', "
        "sent INTEGER DEFAULT 0)"
    )
    await conn.execute(
        "CREATE TABLE IF NOT EXISTS exam_results("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER, "
        "chat_id INTEGER, "
        "user_name TEXT, "
        "score INTEGER, "
        "total INTEGER, "
        "percentage INTEGER, "
        "duration INTEGER, "
        "created_at INTEGER)"
    )
    # Migrations
    await ensure_column(conn, "user_settings", "difficulty", "TEXT DEFAULT 'medium'")
    await ensure_column(conn, "quizzes", "explanation", "TEXT DEFAULT ''")
    await ensure_column(conn, "quizzes", "created_at", "INTEGER DEFAULT 0")
    await ensure_column(conn, "user_settings", "ai_provider", "TEXT DEFAULT 'auto'")
    await ensure_column(conn, "user_settings", "preferred_language", "TEXT DEFAULT 'auto'")
    await ensure_column(conn, "user_settings", "ai_specialty", "TEXT DEFAULT ''")
    await ensure_column(conn, "user_settings", "delivery_mode", "TEXT DEFAULT 'rich'")
    await ensure_column(conn, "user_settings", "share_mode", "TEXT DEFAULT 'both'")
    await ensure_column(conn, "user_settings", "show_explanation", "INTEGER DEFAULT 1")
    await ensure_column(conn, "user_settings", "confirmation_message", "INTEGER DEFAULT 1")
    await ensure_column(conn, "user_settings", "ai_tool_mode", "TEXT DEFAULT 'quiz'")
    await ensure_column(conn, "user_settings", "fun_breaks", "INTEGER DEFAULT 0")
    await ensure_column(conn, "user_settings", "fun_interval", "INTEGER DEFAULT 6")
    await ensure_column(conn, "user_settings", "fun_style", "TEXT DEFAULT 'mixed'")
    await conn.commit()
    logger.info("Database initialized with WAL mode and migrations applied")


async def get_user_settings(user_id: int) -> UserSettings:
    """Fetch user settings, inserting defaults if not existing."""
    conn = await DB.conn()
    row = await (await conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))).fetchone()
    if row is None:
        legacy = await (await conn.execute("SELECT chat_id, title FROM default_channels WHERE user_id=?", (user_id,))).fetchone()
        default_target = legacy["chat_id"] if legacy else None
        default_title = legacy["title"] if legacy else ""
        await conn.execute(
            "INSERT OR IGNORE INTO user_settings("
            "user_id, default_target, default_target_title, delete_source, ai_enabled, ai_model, ai_provider, ai_count, preferred_language, ai_specialty, delivery_mode, share_mode, show_explanation, confirmation_message, ai_tool_mode, fun_breaks, fun_interval, fun_style"
            ") VALUES (?, ?, ?, ?, 1, ?, 'auto', ?, 'auto', '', 'rich', 'both', 1, ?, 'quiz', 0, 6, 'mixed')",
            (
                user_id,
                serialize_target(default_target),
                default_title,
                1 if DEFAULT_DELETE_SOURCE else 0,
                OPENAI_MODEL,
                AI_DEFAULT_COUNT,
                1 if QUIZ_CONFIRMATION_MESSAGE else 0,
            ),
        )
        await conn.commit()
        row = await (await conn.execute("SELECT * FROM user_settings WHERE user_id=?", (user_id,))).fetchone()

    return UserSettings(
        default_target=deserialize_target(row["default_target"]),
        default_target_title=row["default_target_title"] or "",
        delete_source=bool(row["delete_source"]),
        ai_enabled=bool(row["ai_enabled"]),
        ai_model=(row["ai_model"] or OPENAI_MODEL).strip() or OPENAI_MODEL,
        ai_provider=(row["ai_provider"] or "auto").strip().lower() or "auto",
        ai_count=max(1, min(10, int(row["ai_count"] or AI_DEFAULT_COUNT))),
        preferred_language=(row["preferred_language"] or "auto").strip().lower() or "auto",
        ai_specialty=(row["ai_specialty"] or "").strip(),
        delivery_mode=(row["delivery_mode"] or "rich").strip().lower() or "rich",
        share_mode=(row["share_mode"] or "both").strip().lower() or "both",
        show_explanation=bool(row["show_explanation"]),
        confirmation_message=bool(row["confirmation_message"]),
        ai_tool_mode=(row["ai_tool_mode"] or "quiz").strip().lower() or "quiz",
        fun_breaks=bool(row["fun_breaks"]),
        fun_interval=max(1, min(30, int(row["fun_interval"] or 6))),
        fun_style=(row["fun_style"] or "mixed").strip().lower() or "mixed",
        difficulty=(dict(row).get("difficulty") or "medium").strip().lower() or "medium",
    )


async def update_user_settings(user_id: int, **kwargs: Any) -> UserSettings:
    """Update user settings dynamically and return updated object."""
    await get_user_settings(user_id)
    fields = []
    values = []
    for key, value in kwargs.items():
        if key == "default_target":
            fields.append("default_target=?")
            values.append(serialize_target(value))
        elif isinstance(value, bool):
            fields.append(f"{key}=?")
            values.append(1 if value else 0)
        else:
            fields.append(f"{key}=?")
            values.append(value)
    if fields:
        values.append(user_id)
        conn = await DB.conn()
        await conn.execute(f"UPDATE user_settings SET {', '.join(fields)} WHERE user_id=?", tuple(values))
        await conn.commit()
    return await get_user_settings(user_id)


async def save_quiz(
    quiz_id: str,
    question: str,
    options: List[str],
    correct_option: int,
    user_id: int,
    explanation: str = "",
) -> None:
    """Store quiz question and metadata in database."""
    conn = await DB.conn()
    await conn.execute(
        "INSERT OR REPLACE INTO quizzes(quiz_id, question, options, correct_option, user_id, explanation, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (quiz_id, question, json.dumps(options, ensure_ascii=False), correct_option, user_id, explanation, int(time.time())),
    )
    await conn.commit()


async def fetch_quiz(quiz_id: str) -> Optional[Tuple[str, List[str], int, str, int]]:
    """Fetch single quiz tuple (question, options, correct_option, explanation, user_id)."""
    conn = await DB.conn()
    row = await (await conn.execute(
        "SELECT question, options, correct_option, explanation, user_id FROM quizzes WHERE quiz_id=?",
        (quiz_id,),
    )).fetchone()
    if row is None:
        return None
    try:
        options = json.loads(row["options"] or "[]")
    except Exception:
        options = [part for part in (row["options"] or "").split(":::") if part]
    return row["question"], options, row["correct_option"], row["explanation"] or "", row["user_id"] or 0


async def fetch_user_quizzes(user_id: Optional[int] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """Fetch recent quizzes created by user (or all if user_id is None)."""
    conn = await DB.conn()
    if user_id:
        query = "SELECT quiz_id, question, options, correct_option, explanation, created_at FROM quizzes WHERE user_id=? ORDER BY created_at DESC LIMIT ?"
        rows = await (await conn.execute(query, (user_id, limit))).fetchall()
    else:
        query = "SELECT quiz_id, question, options, correct_option, explanation, created_at FROM quizzes ORDER BY created_at DESC LIMIT ?"
        rows = await (await conn.execute(query, (limit,))).fetchall()
    
    result = []
    for r in rows:
        try:
            opts = json.loads(r["options"] or "[]")
        except Exception:
            opts = [part for part in (r["options"] or "").split(":::") if part]
        result.append({
            "quiz_id": r["quiz_id"],
            "question": r["question"] or "",
            "options": opts,
            "correct_option": int(r["correct_option"] or 0),
            "explanation": r["explanation"] or "",
            "created_at": r["created_at"] or 0,
        })
    return result


async def record_stats(user_id: int, target: Target, chat_type: str, title: str) -> None:
    """Record usage counters for user, target, and channels."""
    conn = await DB.conn()
    if user_id:
        await conn.execute("INSERT INTO user_stats(user_id, sent) VALUES(?, 1) ON CONFLICT(user_id) DO UPDATE SET sent=sent+1", (user_id,))
    target_key = serialize_target(target)
    if target_key:
        await conn.execute(
            "INSERT INTO target_stats(target_id, chat_type, title, sent) VALUES(?, ?, ?, 1) "
            "ON CONFLICT(target_id) DO UPDATE SET sent=sent+1, chat_type=excluded.chat_type, title=excluded.title",
            (target_key, chat_type, title),
        )
    if isinstance(target, int) and target < 0:
        await conn.execute("INSERT INTO channel_stats(chat_id, sent) VALUES(?, 1) ON CONFLICT(chat_id) DO UPDATE SET sent=sent+1", (target,))
        await conn.execute(
            "INSERT INTO known_channels(chat_id, title) VALUES(?, ?) ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title",
            (target, title),
        )
    await conn.commit()


async def resolve_user_lang(user_id: int, user_language_code: Optional[str] = None, text: str = "") -> str:
    """Resolve preferred language for user with fallback to message text."""
    if not user_id:
        return infer_lang(None, text)
    settings = await get_user_settings(user_id)
    if settings.preferred_language in {"ar", "en"}:
        return settings.preferred_language
    return infer_lang(user_language_code, text)


async def save_exam_result(
    user_id: int,
    chat_id: int,
    user_name: str,
    score: int,
    total: int,
    percentage: int,
    duration: int,
) -> None:
    """Save an interactive exam result into database."""
    conn = await DB.conn()
    await conn.execute(
        "INSERT INTO exam_results(user_id, chat_id, user_name, score, total, percentage, duration, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, chat_id, user_name or f"User_{user_id}", score, total, percentage, duration, int(time.time())),
    )
    await conn.commit()


async def fetch_leaderboard(chat_id: Optional[int] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch top exam scores overall or for a specific group chat."""
    conn = await DB.conn()
    if chat_id:
        query = (
            "SELECT user_name, score, total, percentage, duration, created_at "
            "FROM exam_results WHERE chat_id=? "
            "ORDER BY percentage DESC, duration ASC, created_at DESC LIMIT ?"
        )
        rows = await (await conn.execute(query, (chat_id, limit))).fetchall()
    else:
        query = (
            "SELECT user_name, score, total, percentage, duration, created_at "
            "FROM exam_results "
            "ORDER BY percentage DESC, duration ASC, created_at DESC LIMIT ?"
        )
        rows = await (await conn.execute(query, (limit,))).fetchall()
    return [dict(r) for r in rows]


async def search_quizzes(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search stored quizzes by keyword."""
    conn = await DB.conn()
    pattern = f"%{query.strip()}%"
    rows = await (await conn.execute(
        "SELECT quiz_id, question, options, correct_option, explanation, created_at "
        "FROM quizzes WHERE question LIKE ? OR explanation LIKE ? ORDER BY created_at DESC LIMIT ?",
        (pattern, pattern, limit),
    )).fetchall()
    results = []
    for r in rows:
        try:
            opts = json.loads(r["options"] or "[]")
        except Exception:
            opts = [p for p in (r["options"] or "").split(":::") if p]
        results.append({
            "quiz_id": r["quiz_id"],
            "question": r["question"],
            "options": opts,
            "correct_option": r["correct_option"],
            "explanation": r["explanation"] or "",
            "created_at": r["created_at"],
        })
    return results


async def fetch_user_exam_stats(user_id: int) -> Dict[str, Any]:
    """Fetch rich aggregated exam statistics for a specific user."""
    conn = await DB.conn()
    row = await (await conn.execute(
        "SELECT COUNT(*) AS total_exams, "
        "COALESCE(AVG(percentage), 0) AS avg_score, "
        "COALESCE(MAX(percentage), 0) AS best_score, "
        "COALESCE(MIN(duration), 0) AS best_time, "
        "COALESCE(SUM(score), 0) AS total_correct, "
        "COALESCE(SUM(total), 0) AS total_questions "
        "FROM exam_results WHERE user_id=?",
        (user_id,),
    )).fetchone()
    if not row or row["total_exams"] == 0:
        return {
            "total_exams": 0,
            "avg_score": 0,
            "best_score": 0,
            "best_time": 0,
            "total_correct": 0,
            "total_questions": 0,
            "rank": "🌱 مبتدئ (Beginner)",
        }

    avg = round(row["avg_score"])
    best = round(row["best_score"])
    count = row["total_exams"]
    if avg >= 90 and count >= 5:
        rank = "👑 أسطوري (Grandmaster)"
    elif avg >= 80:
        rank = "🥇 متقدم (Master)"
    elif avg >= 65:
        rank = "🥈 كفء (Skilled)"
    elif avg >= 50:
        rank = "🥉 مجتاز (Pass)"
    else:
        rank = "🌱 متدرب (Apprentice)"

    return {
        "total_exams": count,
        "avg_score": avg,
        "best_score": best,
        "best_time": row["best_time"],
        "total_correct": row["total_correct"],
        "total_questions": row["total_questions"],
        "rank": rank,
    }
