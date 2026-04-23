# Развёртывание на сервер

Инструкции для deploy-инженера. Никакого Docker — просто Python + cron.

## Предварительно

1. **Сервер** — Ubuntu 22.04+ (любая Linux), 1 vCPU, 1 GB RAM минимум, 5 GB диска
2. **Python 3.10+**
3. **Claude Code CLI** — авторизованный аккаунт с подпиской (Opus требует Pro/Max тариф)
4. **Telegram API-ключи** — от владельца проекта
5. **Telethon-сессия** — скопировать существующую `ai_digest_session.session` с доверенной машины ИЛИ залогиниться заново (нужен SMS с телефона владельца)

---

## Установка

### 1. Зависимости

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git

# Claude Code CLI
curl -fsSL https://claude.ai/install.sh | sh
claude   # авторизоваться аккаунтом с подпиской
```

### 2. Клонирование проекта

```bash
git clone https://github.com/wsgp2/SergD_AI_Digest.git /opt/ai_digest
cd /opt/ai_digest

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Конфигурация

```bash
cp .env.example .env
nano .env
```

Значения — у владельца проекта (API_ID, API_HASH, PHONE, DIGEST_RECIPIENT_ID, CHATLIST_INVITE).

### 4. Telethon-сессия

**Вариант А — скопировать готовую (проще):**
```bash
scp user@work-machine:/Users/user/SergD_AI_Digest/ai_digest_session.session .
chmod 600 ai_digest_session.session
```

**Вариант Б — залогиниться заново:**
```bash
source .venv/bin/activate
python3 -m src.collector 1
# Telethon запросит SMS-код → ввести → сессия сохранится
```

### 5. Тестовый запуск

```bash
python3 -m src.main --skip-send     # без отправки, проверить что всё работает
python3 -m src.main                  # полный прогон
```

### 6. Cron

Дайджест отправляется в 8:00 по Бали (UTC+8) = **00:00 UTC**.

```bash
crontab -e
```

Добавить:
```cron
# AI Digest: ежедневно в 00:00 UTC (8:00 Бали)
PATH=/usr/local/bin:/usr/bin:/bin
0 0 * * * cd /opt/ai_digest && /opt/ai_digest/.venv/bin/python3 -m src.main >> /var/log/ai_digest.log 2>&1
```

Проверить что работает:
```bash
sudo tail -f /var/log/ai_digest.log
```

---

## Рабочие моменты

### Claude CLI не работает из cron

Cron запускается с минимальным `PATH`, и `claude` может не найтись. Решение — задай `PATH` в crontab (см. выше) или пропиши полный путь:

```bash
which claude     # например: /usr/local/bin/claude
```

И в `src/digest.py` замени `"claude"` на полный путь.

### Сессия Telethon отвалилась

Ошибка `AuthKeyUnregistered` = Telegram разлогинил сессию. Лечится перелогином (см. шаг 4 Вариант Б).

### FloodWait от Telegram

Скрипт сам обрабатывает — ждёт указанное время. Если повторяется регулярно — снизь `--hours` или разнеси сбор с разных каналов по времени.

### Добавить каналы

Просто добавь каналы в свою Telegram-папку (которая `CHATLIST_INVITE`) — скрипт подхватит автоматически при следующем запуске.

---

## Мониторинг

```bash
# Последние запуски:
sqlite3 /opt/ai_digest/digest.db "SELECT run_at, stage, status, details FROM run_logs ORDER BY id DESC LIMIT 20"

# Последние дайджесты:
sqlite3 /opt/ai_digest/digest.db "SELECT digest_date, model, posts_count, duration_sec, sent_at FROM digests ORDER BY id DESC LIMIT 10"

# Число постов в БД:
sqlite3 /opt/ai_digest/digest.db "SELECT COUNT(*) FROM posts"
```

---

## Обновление кода

```bash
cd /opt/ai_digest
git pull
source .venv/bin/activate
pip install -r requirements.txt --upgrade
# cron подхватит изменения на следующий запуск
```

---

## Контакты

Сергей Дышкант — [@Sergei_dyshkant](https://t.me/Sergei_dyshkant).
