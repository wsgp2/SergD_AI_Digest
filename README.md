# SergD AI Digest

Утренний AI-дайджест из десятков Telegram-каналов одним сообщением в личку.

**Как работает:** раз в сутки скрипт забирает посты со всех каналов в выбранной Telegram-папке за последние 24 часа, скармливает их Claude (Opus или Sonnet) через CLI, получает Markdown-дайджест с кластеризацией инфоповодов и отправляет в личный чат Telethon'ом.

**Ключевые особенности:**
- Используется подписка Claude Code, а не Anthropic API (→ нет расходов на токены)
- Контекст 1M токенов у Opus 4.7 вмещает хоть 500 постов за раз
- Telegram-markdown с **жирным**, _курсивом_ и синими гиперссылками на оригиналы
- SQLite как единое хранилище постов и дайджестов
- Идемпотентно: можно запускать повторно, ничего не задублируется

---

## Быстрый старт

### 1. Требования

- Python 3.10+
- **Claude Code CLI** ([`brew install claude` или см. docs.claude.com](https://docs.claude.com/en/docs/claude-code/overview)) — авторизованный аккаунт с подпиской, предоставляющей доступ к нужной модели (Opus / Sonnet / Haiku)
- Telegram API-ключи (`my.telegram.org/apps` → API_ID и API_HASH)
- Telegram-папка (chatlist), которой ты хочешь делиться со скриптом

### 2. Клон и зависимости

```bash
git clone https://github.com/wsgp2/SergD_AI_Digest.git
cd SergD_AI_Digest
python3 -m pip install -r requirements.txt
```

### 3. Конфиг

```bash
cp .env.example .env
```

Открой `.env` и заполни:

```ini
API_ID=14749377
API_HASH=abc…
PHONE=+79…
SESSION_NAME=ai_digest_session

DIGEST_RECIPIENT_ID=531712920     # твой user_id (в @userinfobot)
DIGEST_MODEL=opus                 # opus | sonnet | haiku
CHATLIST_INVITE=gp4chTEnoeAzOTJi  # slug из t.me/addlist/<slug>

TIMEZONE=Asia/Makassar            # для кронов и timestamps (IANA)
DIGEST_HOUR=8                     # локальный час отправки
```

### 4. Первый прогон (полный)

```bash
python3 -m src.main
```

Что произойдёт:
1. Telethon запросит код из Telegram при первом логине (сессия сохранится)
2. Импортирует каналы из `CHATLIST_INVITE` в твои подписки
3. Соберёт посты за последние 24 часа в SQLite
4. Вызовет `claude -p --model opus` → получит Markdown-дайджест
5. Отправит его на `DIGEST_RECIPIENT_ID`

### 5. Отдельные операции

```bash
# только собрать новые посты (без генерации и отправки)
python3 -m src.collector 24

# только сгенерировать дайджест из уже собранных постов
python3 -m src.digest opus
python3 -m src.digest sonnet

# пайплайн без отправки (для теста)
python3 -m src.main --skip-send

# отправить существующий дайджест по ID
python3 -m src.main --only-send 1
```

---

## Архитектура

```
         ┌────────────────┐
         │ 26 Telegram-   │
         │ каналов (AI,   │
         │ tech, бизнес)  │
         └────────┬───────┘
                  │ Telethon
                  ▼
         ┌────────────────┐      ┌──────────────────┐
         │ collector.py   │ ───▶ │ digest.db        │
         │ — iter_messages│      │ — channels       │
         │ — dedupe upsert│      │ — posts          │
         └────────────────┘      │ — digests        │
                                 │ — run_logs       │
         ┌────────────────┐      └──────────┬───────┘
         │ digest.py      │ ◀──── читает ───┘
         │ — строит prompt│
         │ — `claude -p`  │
         │ — сохраняет .md│
         └────────┬───────┘
                  │ текст дайджеста
                  ▼
         ┌────────────────┐
         │ sender.py      │ ───▶ Telegram personal
         │ — Telethon send│       (DIGEST_RECIPIENT_ID)
         │ — markdown     │
         │ — сплит 4096   │
         └────────────────┘
```

### Состав

```
SergD_AI_Digest/
├── src/
│   ├── config.py       # читает .env
│   ├── db.py           # SQLite + upsert-хелперы
│   ├── collector.py    # Telethon, сбор постов
│   ├── digest.py       # промпт + subprocess claude CLI
│   ├── sender.py       # отправка в Telegram
│   └── main.py         # оркестратор CLI
├── db_schema.sql       # 4 таблицы (channels / posts / digests / run_logs)
├── requirements.txt
├── .env.example
├── .gitignore
├── deploy/             # Docker, systemd, инструкции по развёртыванию
└── output/             # gitignored, куда сохраняются сгенерированные .md
```

### Что лежит в БД

| Таблица | Что хранит |
|---------|-----------|
| `channels` | Каналы (channel_id, username, title, access_hash) |
| `posts` | Все собранные посты (text, views, forwards, reactions, url, media_type) |
| `digests` | Сгенерированные дайджесты (дата, модель, content, токены, duration, sent_at) |
| `run_logs` | Логи запусков каждого стейджа (collect/digest/send) — для дебага |

Все upsert'ы по составным ключам — **запускай скрипт хоть каждые 5 минут, дубликатов не будет**.

---

## Модели

| Модель | Качество дайджеста | Цена | Время на 100 постов |
|--------|---------------------|------|---------------------|
| `opus` (Claude Opus 4.7 1M) | Лучшее | премиум-тариф | ~3-4 мин |
| `sonnet` (Claude Sonnet 4.6) | Отличное | стандарт | ~5 мин |
| `haiku` (Claude Haiku 4.5) | Хорошее | базовый | ~1-2 мин |

Переключается флагом `--model` или в `.env`. Для продакшена рекомендуем Opus (если есть подписка) или Sonnet.

---

## Развёртывание на сервер

См. [`deploy/README.md`](deploy/README.md) — пошаговая инструкция.

Короткая версия:
1. Установить Claude Code CLI на сервер и авторизоваться
2. Склонить репо, заполнить `.env`
3. Скопировать авторизованную Telethon-сессию (или залогиниться заново)
4. Добавить cron-job: `0 0 * * * cd /path && python3 -m src.main` (00:00 UTC = 8:00 Бали)

---

## Формат дайджеста

```
🌅 **AI-дайджест 2026-04-23** · 20 инфоповодов · 71 пост из 17 каналов

━━━━━━━━━━━━━━━━━━━━

1. 🤖 [**Alibaba представила Qwen3.6: 1T параметров**](https://t.me/bezsmuzi/15521)
   📊 2 канала · 4.3K просмотров
   _Вышли Qwen3.6-Max-Preview с 1 трлн параметров и Qwen3.6-27B…_

2. 🚀 [**OpenAI затизерила GPT-5.5**](https://t.me/lama_channel_gpt/2780)
   📊 1 канал · 3.2K просмотров
   _..._

...
```

Эмодзи-категории: 🚀 запуски · 💰 инвестиции · 🤖 модели · 🎨 контент · 🛠 инструменты · 📊 рынок · 💼 бизнес · 🐛 инциденты.

---

## Безопасность и креды

- `.env` в `.gitignore` — никогда не коммитится
- Файлы сессий `*.session` тоже в `.gitignore`
- БД `digest.db` игнорируется (содержит тексты постов)
- При публикации репозитория — убедись что в истории git нет `.env` (используй `git log --all -- .env`)

---

## Лицензия

MIT.

---

## Автор

Сергей Дышкант — [@Sergei_dyshkant](https://t.me/Sergei_dyshkant)
