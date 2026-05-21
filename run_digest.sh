#!/bin/bash
# AI Digest runner — для cron
cd /home/ubuntu/bots/ai_digest

# Загружаем переменные из .env
set -a
source .env
set +a

# Запускаем pipeline, лог в logs/ai_digest.log
/home/ubuntu/bots/ai_digest/venv/bin/python3 -u -m src.main >> /home/ubuntu/bots/ai_digest/logs/ai_digest.log 2>&1
