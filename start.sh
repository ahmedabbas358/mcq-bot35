#!/usr/bin/env bash
set -e

echo "========================================================"
echo "              🤖 MCQ Bot 35 Launcher"
echo "========================================================"
echo ""

if [ ! -f .env ]; then
    echo "[!] .env file not found. Creating from .env.example..."
    cp .env.example .env
    echo "[*] Please edit .env and configure your TELEGRAM_BOT_TOKEN!"
fi

python3 manage.py check
echo ""
echo "[*] Starting bot in daemon loop..."
until python3 main.py; do
    echo "[!] Bot stopped with exit code $?. Restarting in 3 seconds..." >&2
    sleep 3
done
