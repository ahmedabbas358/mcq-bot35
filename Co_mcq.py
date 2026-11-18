"""
mcq-bot35 Canonical Entry Point and Backward-Compatibility Layer.
Delegates to modular bot architecture.
"""

from bot.app import (
    build_application,
    main,
    on_shutdown,
    post_init,
    schedule_cleanup,
)
from bot.config import (
    AI_API_KEY,
    AI_DEFAULT_COUNT,
    AI_MAX_SOURCE_CHARS,
    AI_OFFLINE_FALLBACK,
    DB_PATH,
    DEFAULT_DELETE_SOURCE,
    ENABLE_WEB_PREVIEW,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    MAX_OPTION_LENGTH,
    MAX_QUESTION_LENGTH,
    MAX_QUEUE_SIZE,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    PORT,
    PUBLIC_BASE_URL,
    QUIZ_CONFIRMATION_MESSAGE,
    SEND_INTERVAL,
    TELEGRAM_BOT_USERNAME,
    SendItem,
    Target,
    UserSettings,
    env_bool,
    env_int,
    resolve_public_base_url,
)
from bot.constants import (
    AI_PROVIDER_CATALOG,
    AI_TOOL_CATALOG,
    FREE_MODEL_CATALOG,
    MCQ_OPTION_PATTERNS,
)
from bot.database import (
    DB,
    fetch_quiz,
    get_user_settings,
    init_db,
    record_stats,
    save_quiz,
    update_user_settings,
)
from bot.i18n.texts import TEXTS, get_text
from bot.parsers.mcq_parser import (
    get_options_blob,
    looks_like_mcq_batch,
    parse_mcq,
    parse_options_blob,
    parse_single_mcq,
    validate_mcq,
)
from bot.services.ai_service import (
    ai_service_available,
    generate_ai_tool_text,
    generate_quizzes_with_ai,
    get_openai_client,
    resolve_ai_runtime,
)
from bot.services.offline_ai import (
    local_ai_tool_text,
    local_fun_break_message,
    local_quiz_pack,
    local_study_pack,
)
from bot.utils.helpers import (
    deserialize_target,
    extract_message_text,
    format_target_label,
    has_arabic,
    humanize_toggle,
    infer_lang,
    normalize_ai_provider,
    normalize_ai_tool,
    parse_ai_count_and_payload,
    parse_target_reference,
    remove_bot_mentions,
    resolve_chat_title,
    serialize_target,
    should_delete_source_message,
)
from bot.utils.queue_manager import (
    enqueue_mcq,
    enqueue_quiz_items,
    ensure_sender,
    prepare_quiz_poll_payload,
    send_queues,
    sender_tasks,
)

if __name__ == "__main__":
    main()
