#!/bin/bash
until python bot.py; do
  echo "Bot crashed with exit code $?. Restarting..." >&2
  sleep 3
done
