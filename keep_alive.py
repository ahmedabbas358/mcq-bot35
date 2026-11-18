"""
Backward-compatible wrapper for keep_alive preview server.
Delegates to modular bot.web.app.
"""

from bot.web.app import (
    app,
    fetch_quiz_record as fetch_quiz,
    keep_alive,
    render_page,
    run_web as run,
)

__all__ = ["app", "keep_alive", "run", "fetch_quiz", "render_page"]

if __name__ == "__main__":
    run()
