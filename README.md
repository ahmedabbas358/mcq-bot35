# mcq-bot35

Telegram bot for publishing MCQ quizzes in private chats, groups, and channels.

## Highlights

- Modernized handling for private chats, groups, and channels.
- Better mention/reply flow in groups.
- Safe auto-detection for clear MCQ batches in groups, even without mentioning the bot every time.
- Default publishing target with `/setchannel` and `/publishhere`.
- Share, repost, and explanation buttons for generated quizzes.
- Optional OpenAI integration for:
  - `/ai <topic>` to generate quizzes from a topic.
  - `/quizify <text>` to turn source text into quizzes.
- AI provider presets with `/providers`, `/provider <name>`, and `/freeai` for the best available free profile.
- Smart AI tools for summaries, translation, flashcards, simplification, riddles, jokes, icebreakers, and more.
- Optional group entertainment breaks that appear after a configurable number of quizzes.
- Optional free local AI through Ollama using the same OpenAI-compatible client, plus Gemini API support for hosted free-tier use when `GEMINI_API_KEY` is available.
- Recommended free local models: `qwen2.5:14b-instruct`, `gemma3:12b`, `qwen2.5:7b`, and `llama3.1:8b`.
- Offline AI fallback for quizzes and study tools when no live model endpoint is configured.
- Per-user settings for target, AI model, AI count, and source-message deletion.
- External quiz preview pages for sharing on Telegram, WhatsApp, X, and other apps.
- Runtime controls for share mode, explanation button, confirmation message, language, AI tool mode, fun breaks, and health checks.
- Inline control panels for language, providers, free models, tools, study mode, delivery mode, share mode, batch size, and fun preferences.

## Required environment variables

- `TELEGRAM_BOT_TOKEN`

## Optional environment variables

- `DB_PATH=stats.db`
- `AI_API_KEY=...`
- `OPENAI_BASE_URL=`
- `OPENAI_MODEL=gpt-5.4-mini`
- `OPENAI_REASONING_EFFORT=low`
- `GEMINI_API_KEY=...`
- `GEMINI_MODEL=gemini-2.5-flash`
- `AI_DEFAULT_COUNT=3`
- `AI_MAX_SOURCE_CHARS=4000`
- `AI_OFFLINE_FALLBACK=true`
- `PRESERVE_TARGET_ORDER=true`
- `GROUP_AUTO_PARSE_MCQS=true`
- `AI_BACKEND_FAILURE_COOLDOWN=300`
- `DELETE_SOURCE_MESSAGES=false`
- `QUIZ_CONFIRMATION_MESSAGE=true`
- `ENABLE_WEB_PREVIEW=true`
- `PUBLIC_BASE_URL=https://your-app-domain.com`
- `TELEGRAM_BOT_USERNAME=your_bot_username`
- `CONCURRENT_UPDATES=64`
- `GLOBAL_SEND_LIMIT=100`
- `LONG_POLL_TIMEOUT=30`
- `MAX_QUEUE_SIZE=2500`
- `MAX_MCQ_BLOCK_LINES=240`
- `MAX_CONCURRENT_SEND=8`
- `SEND_INTERVAL=0.15`
- `FAST_SEND_INTERVAL=0.03`

## Commands

- `/start`
- `/help`
- `/settings`
- `/controls`
- `/stats`
- `/setchannel <chat_id|@channel|here>`
- `/publishhere`
- `/clearchannel`
- `/toggledelete`
- `/toggleai`
- `/setmodel <model-id>` - override the model and auto-pick the right provider when possible
- `/providers`
- `/provider <name>`
- `/freeai` - choose the best available free AI profile
- `/aidiag`
- `/freemodels`
- `/freemodel <name>`
- `/setcount <1-10>`
- `/language <auto|ar|en>`
- `/lang <auto|ar|en>`
- `/specialty <text|clear>`
- `/delivery <fast|rich>`
- `/sharemode <telegram|web|both>`
- `/toggleexplain`
- `/toggleconfirm`
- `/tools`
- `/tool <mode>`
- `/ask [tool] <text>`
- `/funmode <on|off>`
- `/funrate <1-30>`
- `/funstyle <mixed|trivia|joke|riddle|icebreaker|poll|study>`
- `/health`
- `/examples`
- `/ai <topic>`
- `/study <text>`
- `/quizify <text>` or reply to a message with `/quizify`

The bot supports Arabic and English interfaces through `/language <auto|ar|en>` or the `/lang` shortcut.
In groups, the bot can now auto-process obvious MCQ batches without a mention when `GROUP_AUTO_PARSE_MCQS=true`, while normal group chat remains ignored.

## Free local AI with Ollama

If you want a free local setup instead of a paid API:

1. Install Ollama.
2. Pull the recommended free model `qwen2.5:14b-instruct`.
3. Set:

```bash
OPENAI_BASE_URL=http://localhost:11434/v1
AI_API_KEY=ollama
OPENAI_MODEL=qwen2.5:14b-instruct
```

The bot will keep using the same OpenAI-compatible client but send requests to your local Ollama server.
If you want other free local options, open `/freemodels` in the bot and pick `gemma3:12b`, `gemma3:4b`, `qwen2.5:7b`, or `llama3.1:8b`.
If you want the best practical free option on hosting, set `GEMINI_API_KEY` and use the Gemini provider preset from `/providers` or `/freeai`.
If no local model server is running, the bot still falls back to a local study engine for quizzes, study packs, and lightweight AI tools.
For external share preview links, set `PUBLIC_BASE_URL` when you can, or let the app auto-detect the public Railway / Render / Vercel URL if your platform exposes one.

## AI control panel

Use `/controls` to open the full inline control panel. From there you can switch language, choose a provider preset, choose a free local model, change the AI tool, open study mode, adjust delivery and share modes, and tune group fun breaks without memorizing commands.
Use `/aidiag` to inspect whether the bot is using a live provider or the offline fallback engine.
If a model choice seems ignored, check whether the matching provider is selected too. The bot now tries to infer the right provider for local models and Gemini models automatically.

## Run locally

```bash
pip install -r requirements.txt
python Co_mcq.py
```

## Important security note

If your Telegram bot token was ever committed into this project, rotate it in BotFather immediately and keep it only in environment variables.
