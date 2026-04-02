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
- Optional free local AI through Ollama using the same OpenAI-compatible client.
- Per-user settings for target, AI model, AI count, and source-message deletion.
- External quiz preview pages for sharing on Telegram, WhatsApp, X, and other apps.
- Runtime controls for share mode, explanation button, confirmation message, and health checks.

## Required environment variables

- `TELEGRAM_BOT_TOKEN`

## Optional environment variables

- `DB_PATH=stats.db`
- `AI_API_KEY=...`
- `OPENAI_BASE_URL=`
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
- `MAX_QUEUE_SIZE=200`
- `MAX_CONCURRENT_SEND=8`
- `SEND_INTERVAL=0.15`
- `FAST_SEND_INTERVAL=0.03`

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
- `/language <auto|ar|en>`
- `/specialty <text|clear>`
- `/delivery <fast|rich>`
- `/sharemode <telegram|web|both>`
- `/toggleexplain`
- `/toggleconfirm`
- `/health`
- `/examples`
- `/ai <topic>`
- `/quizify <text>` or reply to a message with `/quizify`

## Free local AI with Ollama

If you want a free local setup instead of a paid API:

1. Install Ollama.
2. Pull a model such as `qwen3:8b`.
3. Set:

```bash
OPENAI_BASE_URL=http://localhost:11434/v1
AI_API_KEY=ollama
OPENAI_MODEL=qwen3:8b
```

The bot will keep using the same OpenAI-compatible client but send requests to your local Ollama server.

## Run locally

```bash
pip install -r requirements.txt
python Co_mcq.py
```

## Important security note

If your Telegram bot token was ever committed into this project, rotate it in BotFather immediately and keep it only in environment variables.
