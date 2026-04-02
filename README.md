# mcq-bot35

Telegram bot for publishing MCQ quizzes in private chats, groups, and channels.

## Highlights

- Modernized handling for private chats, groups, and channels.
- Better mention/reply flow in groups.
- Default publishing target with `/setchannel` and `/publishhere`.
- Share, repost, and explanation buttons for generated quizzes.
- Optional OpenAI integration for:
  - `/ai <topic>` to generate quizzes from a topic.
  - `/quizify <text>` to turn source text into quizzes.
- Per-user settings for target, AI model, AI count, and source-message deletion.
- External quiz preview pages for sharing on Telegram, WhatsApp, X, and other apps.
- Runtime controls for share mode, explanation button, confirmation message, and health checks.

## Required environment variables

- `TELEGRAM_BOT_TOKEN`

## Optional environment variables

- `DB_PATH=stats.db`
- `OPENAI_API_KEY=...`
- `OPENAI_MODEL=gpt-5.4-mini`
- `OPENAI_REASONING_EFFORT=low`
- `AI_DEFAULT_COUNT=3`
- `AI_MAX_SOURCE_CHARS=4000`
- `DELETE_SOURCE_MESSAGES=false`
- `QUIZ_CONFIRMATION_MESSAGE=true`
- `ENABLE_WEB_PREVIEW=true`
- `PUBLIC_BASE_URL=https://your-app-domain.com`
- `TELEGRAM_BOT_USERNAME=your_bot_username`
- `CONCURRENT_UPDATES=64`
- `GLOBAL_SEND_LIMIT=100`
- `LONG_POLL_TIMEOUT=30`

## Commands

- `/start`
- `/help`
- `/settings`
- `/stats`
- `/setchannel <chat_id|@channel|here>`
- `/publishhere`
- `/clearchannel`
- `/toggledelete`
- `/toggleai`
- `/setmodel <model-id>`
- `/setcount <1-10>`
- `/sharemode <telegram|web|both>`
- `/toggleexplain`
- `/toggleconfirm`
- `/health`
- `/ai <topic>`
- `/quizify <text>` or reply to a message with `/quizify`

## Run locally

```bash
pip install -r requirements.txt
python Co_mcq.py
```

## Important security note

If your Telegram bot token was ever committed into this project, rotate it in BotFather immediately and keep it only in environment variables.
