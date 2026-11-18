"""
Services package: AI backends, offline engines, exam sessions, and quiz exporters.
"""

from bot.services.ai_service import (
    ai_service_available,
    generate_ai_tool_text,
    generate_quizzes_with_ai,
    get_openai_client,
    resolve_ai_runtime,
)
from bot.services.exam_service import (
    finish_exam,
    get_exam,
    start_exam,
)
from bot.services.exporter import (
    create_export_file,
    export_to_csv,
    export_to_gift,
    export_to_json,
    export_to_text,
)
from bot.services.offline_ai import (
    local_ai_tool_text,
    local_fun_break_message,
    local_quiz_pack,
    local_study_pack,
)

__all__ = [
    "resolve_ai_runtime",
    "get_openai_client",
    "ai_service_available",
    "generate_quizzes_with_ai",
    "generate_ai_tool_text",
    "local_fun_break_message",
    "local_quiz_pack",
    "local_study_pack",
    "local_ai_tool_text",
    "start_exam",
    "get_exam",
    "finish_exam",
    "export_to_json",
    "export_to_csv",
    "export_to_gift",
    "export_to_text",
    "create_export_file",
]
