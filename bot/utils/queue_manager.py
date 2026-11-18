"""
Queue manager, background sender workers, rate limiters, and Telegram dispatcher.
"""

import asyncio
from collections import defaultdict
import contextlib
import hashlib
import logging
import random
import time
from typing import Dict, List, Optional, Set, Tuple

try:
    import telegram
    from telegram import Message, Poll
    from telegram.constants import ChatType
    from telegram.ext import ContextTypes
except ImportError:
    telegram = None
    class _Mock:
        pass
    Message = _Mock
    Poll = _Mock
    ChatType = _Mock
    ContextTypes = _Mock

from bot.config import (
    AI_DEFAULT_COUNT,
    CONCURRENT_UPDATES,
    DEFAULT_DELETE_SOURCE,
    FAST_SEND_INTERVAL,
    GLOBAL_SEND_LIMIT,
    MAX_CONCURRENT_SEND,
    MAX_QUEUE_SIZE,
    OPENAI_MODEL,
    PRESERVE_TARGET_ORDER,
    QUIZ_CONFIRMATION_MESSAGE,
    SEND_INTERVAL,
    SendItem,
    Target,
    UserSettings,
)
from bot.database import (
    get_user_settings,
    record_stats,
    save_quiz,
)
from bot.i18n.texts import get_text
from bot.keyboards.inline import build_quiz_keyboard
from bot.parsers.mcq_parser import parse_mcq, validate_mcq
from bot.services.ai_service import (
    ai_service_available,
    generate_ai_tool_text,
)
from bot.services.offline_ai import local_fun_break_message
from bot.utils.helpers import (
    extract_message_text,
    infer_lang,
    normalize_fun_style,
    resolve_chat_title,
    should_delete_source_message,
)

logger = logging.getLogger(__name__)

# State collections
send_queues: Dict[Target, asyncio.Queue] = defaultdict(lambda: asyncio.Queue(maxsize=MAX_QUEUE_SIZE))
sender_tasks: Dict[Target, List[asyncio.Task]] = defaultdict(list)
deleted_source_messages: Set[Tuple[int, int]] = set()
global_send_semaphore = asyncio.Semaphore(GLOBAL_SEND_LIMIT)
quiz_answer_rotation_state: Dict[str, int] = defaultdict(int)
group_interlude_state: Dict[str, Dict[str, int]] = defaultdict(dict)
group_interlude_lock = asyncio.Lock()


async def resolve_target_chat(bot, target: Target):
    try:
        return await bot.get_chat(target)
    except Exception as exc:
        logger.warning("Could not resolve target chat %s: %s", target, exc)
        return None


async def resolve_target_chat_type(bot, target: Target) -> str:
    chat = await resolve_target_chat(bot, target)
    return getattr(chat, "type", "") if chat else ""


def prepare_quiz_poll_payload(item: SendItem, target: Target) -> Tuple[List[str], int]:
    options = list(item.options or [])
    if not options:
        return options, item.correct_option
    correct_index = item.correct_option if 0 <= item.correct_option < len(options) else 0
    if len(options) == 1:
        return options, correct_index

    rotation_key = f"{item.owner_user_id or 0}:{target}"
    desired_position = quiz_answer_rotation_state[rotation_key] % len(options)
    quiz_answer_rotation_state[rotation_key] = quiz_answer_rotation_state[rotation_key] + 1

    correct_option = options[correct_index]
    distractors = [option for idx, option in enumerate(options) if idx != correct_index]
    seed_bytes = hashlib.sha256(f"{item.quiz_id}:{rotation_key}:{desired_position}".encode()).digest()
    rnd = random.Random(int.from_bytes(seed_bytes[:8], "big"))
    rnd.shuffle(distractors)

    shuffled = distractors[:desired_position] + [correct_option] + distractors[desired_position:]
    if len(shuffled) != len(options):
        shuffled = options
        desired_position = correct_index
    return shuffled, desired_position


def ensure_sender(target: Target, context: ContextTypes.DEFAULT_TYPE) -> None:
    active_tasks = [task for task in sender_tasks[target] if not task.done()]
    sender_tasks[target] = active_tasks
    target_sender_limit = 1 if PRESERVE_TARGET_ORDER else max(1, MAX_CONCURRENT_SEND)
    missing = target_sender_limit - len(active_tasks)
    for worker_idx in range(missing):
        task = context.application.create_task(_sender(target, context, worker_idx + len(active_tasks) + 1))
        sender_tasks[target].append(task)


async def _sender(target: Target, context: ContextTypes.DEFAULT_TYPE, worker_idx: int) -> None:
    logger.info("Sender task started for target %s worker %s", target, worker_idx)
    try:
        while True:
            item: SendItem = await send_queues[target].get()
            async with global_send_semaphore:
                try:
                    target_chat_type = await resolve_target_chat_type(context.bot, target)
                    poll_options, poll_correct_index = prepare_quiz_poll_payload(item, target)
                    
                    # Pass explanation directly into Telegram quiz poll (up to 200 chars supported natively)
                    explanation_payload = (item.explanation or "").strip()[:200] if item.explanation else None

                    sent_message = await context.bot.send_poll(
                        chat_id=target,
                        question=item.question,
                        options=poll_options,
                        type=Poll.QUIZ,
                        correct_option_id=poll_correct_index,
                        explanation=explanation_payload,
                        is_anonymous=target_chat_type == ChatType.CHANNEL,
                    )

                    await save_quiz(
                        quiz_id=item.quiz_id,
                        question=item.question,
                        options=poll_options,
                        correct_option=poll_correct_index,
                        user_id=item.owner_user_id,
                        explanation=item.explanation,
                    )
                    
                    owner_settings = await get_user_settings(item.owner_user_id) if item.owner_user_id else UserSettings(
                        None, "", DEFAULT_DELETE_SOURCE, True, OPENAI_MODEL, "auto",
                        AI_DEFAULT_COUNT, "auto", "", "rich", "both", True,
                        QUIZ_CONFIRMATION_MESSAGE, "quiz", False, 6, "mixed"
                    )

                    if item.delete_source and item.source_chat_id and item.source_message_id:
                        delete_key = (item.source_chat_id, item.source_message_id)
                        if (
                            delete_key not in deleted_source_messages
                            and should_delete_source_message(item.delete_source, item.source_chat_type, item.source_chat_id)
                        ):
                            with contextlib.suppress(Exception):
                                await context.bot.delete_message(chat_id=item.source_chat_id, message_id=item.source_message_id)
                                deleted_source_messages.add(delete_key)
                                if len(deleted_source_messages) > 5000:
                                    deleted_source_messages.clear()

                    await record_stats(
                        user_id=item.owner_user_id,
                        target=target,
                        chat_type=sent_message.chat.type,
                        title=resolve_chat_title(sent_message.chat),
                    )

                    if owner_settings.confirmation_message and owner_settings.delivery_mode != "fast":
                        keyboard = await build_quiz_keyboard(
                            context,
                            quiz_id=item.quiz_id,
                            lang=item.lang,
                            include_explanation=bool(item.explanation) and owner_settings.show_explanation,
                            share_mode=owner_settings.share_mode,
                            question=item.question,
                        )
                        with contextlib.suppress(Exception):
                            await context.bot.send_message(
                                chat_id=target,
                                text=get_text("quiz_sent", item.lang),
                                reply_markup=keyboard,
                            )

                    await maybe_send_group_interlude(context, target, target_chat_type, owner_settings, item.lang)

                    wait_interval = FAST_SEND_INTERVAL if owner_settings.delivery_mode == "fast" else SEND_INTERVAL
                    if wait_interval > 0:
                        await asyncio.sleep(wait_interval)
                except telegram.error.RetryAfter as exc:
                    retry_wait = exc.retry_after + 0.5
                    logger.warning("Flood control hit for target %s. Waiting %s seconds...", target, retry_wait)
                    # Re-queue item to not lose it
                    with contextlib.suppress(Exception):
                        await send_queues[target].put(item)
                    await asyncio.sleep(retry_wait)
                except telegram.error.BadRequest as exc:
                    logger.warning("BadRequest while sending poll to %s: %s", target, exc)
                    await asyncio.sleep(1)
                except Exception as exc:
                    logger.exception("Error sending poll to %s: %s", target, exc)
                    await asyncio.sleep(3)
    except asyncio.CancelledError:
        logger.info("Sender task cancelled for %s worker %s", target, worker_idx)
        raise


async def enqueue_quiz_items(
    target: Target,
    quizzes: List[Tuple[str, List[str], int, str]],
    context: ContextTypes.DEFAULT_TYPE,
    owner_user_id: int,
    lang: str,
    source_chat_id: Optional[int] = None,
    source_chat_type: str = "",
    source_message_id: Optional[int] = None,
    delete_source: bool = False,
) -> int:
    """Validate, digest, and queue quizzes for target delivery."""
    queued = 0
    for item in quizzes:
        question = item[0]
        options = item[1]
        correct_index = item[2]
        explanation = item[3] if len(item) > 3 else ""
        if not validate_mcq(question, options):
            continue
        quiz_id = hashlib.md5((question + ":::" + ":::".join(options)).encode()).hexdigest()
        await send_queues[target].put(
            SendItem(
                target=target,
                question=question,
                options=options,
                correct_option=correct_index,
                quiz_id=quiz_id,
                explanation=explanation,
                owner_user_id=owner_user_id,
                source_chat_id=source_chat_id,
                source_chat_type=source_chat_type,
                source_message_id=source_message_id,
                delete_source=delete_source,
                lang=lang,
            )
        )
        queued += 1
    if queued:
        ensure_sender(target, context)
    return queued


async def enqueue_mcq(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    explicit_target: Optional[Target] = None,
    owner_user_id: int = 0,
    is_private: bool = False,
    notify_fail: bool = False,
    text_override: Optional[str] = None,
) -> bool:
    """Parse text and enqueue questions."""
    raw_message_text = extract_message_text(message)
    try:
        settings = await get_user_settings(owner_user_id) if owner_user_id else UserSettings(
            None, "", DEFAULT_DELETE_SOURCE, True, OPENAI_MODEL, "auto", AI_DEFAULT_COUNT, "auto", "", "rich", "both", True, QUIZ_CONFIRMATION_MESSAGE, "quiz", False, 6, "mixed"
        )
    except Exception:
        settings = UserSettings(None, "", DEFAULT_DELETE_SOURCE, True, OPENAI_MODEL, "auto", AI_DEFAULT_COUNT, "auto", "", "rich", "both", True, QUIZ_CONFIRMATION_MESSAGE, "quiz", False, 6, "mixed")
    lang = settings.preferred_language if settings.preferred_language in {"ar", "en"} else infer_lang(getattr(message.from_user, "language_code", None), raw_message_text)

    target = explicit_target or settings.default_target or message.chat.id
    raw_text = text_override if text_override is not None else raw_message_text

    try:
        results = parse_mcq(raw_text)
    except Exception as exc:
        logger.exception("Parsing failed: %s", exc)
        if notify_fail:
            with contextlib.suppress(Exception):
                await message.reply_text(get_text("invalid_format", lang))
        return False

    if not results:
        if notify_fail:
            with contextlib.suppress(Exception):
                await message.reply_text(get_text("no_q", lang))
        return False

    quiz_items = []
    for res in results:
        if len(res) >= 4:
            quiz_items.append(res[:4])
        else:
            quiz_items.append((res[0], res[1], res[2], ""))

    try:
        queued = await enqueue_quiz_items(
            target=target,
            quizzes=quiz_items,
            context=context,
            owner_user_id=owner_user_id,
            lang=lang,
            source_chat_id=message.chat.id,
            source_chat_type=message.chat.type,
            source_message_id=message.message_id,
            delete_source=should_delete_source_message(settings.delete_source, message.chat.type, message.chat.id),
        )
        if queued > 0 and is_private:
            with contextlib.suppress(Exception):
                await message.reply_text(get_text("batch_success", lang, count=queued))
    except asyncio.QueueFull:
        if notify_fail:
            with contextlib.suppress(Exception):
                await message.reply_text(get_text("queue_full", lang))
        return False

    return True


async def maybe_send_group_interlude(
    context: ContextTypes.DEFAULT_TYPE,
    target: Target,
    target_chat_type: str,
    owner_settings: UserSettings,
    lang: str,
) -> None:
    if not owner_settings.fun_breaks or target_chat_type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    interval = max(1, min(30, int(owner_settings.fun_interval or 6)))
    key = str(target)
    should_fire = False
    async with group_interlude_lock:
        state = group_interlude_state[key]
        state["count"] = int(state.get("count", 0)) + 1
        if state["count"] >= interval:
            now = int(time.time())
            if now - int(state.get("last", 0)) >= 45:
                state["count"] = 0
                state["last"] = now
                should_fire = True
    if not should_fire:
        return

    selected_style = normalize_fun_style(owner_settings.fun_style)
    if selected_style == "mixed":
        fun_tool = random.choice(["trivia", "joke", "riddle", "icebreaker", "poll", "study"])
    else:
        fun_tool = selected_style if selected_style in {"joke", "riddle", "icebreaker", "poll", "study"} else "trivia"
    text = None
    if ai_service_available(owner_settings):
        try:
            text = await generate_ai_tool_text(
                fun_tool,
                "Create one short group break that is playful, relevant, and not distracting.",
                lang,
                owner_settings.ai_model,
                owner_settings.ai_specialty,
                settings=owner_settings,
            )
        except Exception:
            text = None
    if not text:
        text = local_fun_break_message(selected_style, lang)
    header = get_text("fun_break_header", lang)
    with contextlib.suppress(Exception):
        await context.bot.send_message(chat_id=target, text=f"{header}: {text}")
