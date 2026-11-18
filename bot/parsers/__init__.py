"""
Parsers package for MCQs, batch blocks, and uploaded files.
"""

from bot.parsers.mcq_parser import (
    get_options_blob,
    looks_like_mcq_batch,
    parse_mcq,
    parse_options_blob,
    parse_single_mcq,
    validate_mcq,
)
from bot.parsers.document_parser import (
    extract_text_from_bytes,
    read_telegram_file,
)

__all__ = [
    "get_options_blob",
    "parse_options_blob",
    "validate_mcq",
    "looks_like_mcq_batch",
    "parse_single_mcq",
    "parse_mcq",
    "extract_text_from_bytes",
    "read_telegram_file",
]
