#!/bin/bash
until python Co_mcq.py; do
  echo "Bot crashed with exit code $?. Restarting..." >&2
  sleep 3
done
