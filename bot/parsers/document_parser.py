"""
Document and file parser for extracting questions and study texts from uploaded files (.txt, etc.).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def extract_text_from_bytes(data: bytes, filename: str = "") -> Optional[str]:
    """Decode raw bytes of a file using common encodings."""
    encodings = ["utf-8", "utf-8-sig", "utf-16", "cp1256", "windows-1256", "latin-1"]
    for enc in encodings:
        try:
            text = data.decode(enc).strip()
            if text:
                return text
        except (UnicodeDecodeError, LookupError):
            continue
    return None


async def read_telegram_file(bot, file_id: str, filename: str = "") -> Optional[str]:
    """Download and decode a Telegram document file."""
    try:
        tg_file = await bot.get_file(file_id)
        file_bytes = await tg_file.download_as_bytearray()
        return extract_text_from_bytes(bytes(file_bytes), filename)
    except Exception as exc:
        logger.warning("Failed to read telegram document %s (%s): %s", file_id, filename, exc)
        return None
