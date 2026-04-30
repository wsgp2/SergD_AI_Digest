"""Генерация дайджеста через `claude -p` (Claude Code CLI subscription)."""
import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from . import config
from .db import connect, save_digest


PROMPT_TEMPLATE = """Ты — AI-news куратор. Ниже JSON с __N_POSTS__ постами из __N_CHANNELS__ Telegram-каналов по темам: AI, ML, tech-стартапы, программирование, бизнес. Все посты опубликованы за последние 24 часа.

Твоя задача — сгенерировать УТРЕННИЙ ДАЙДЖЕСТ для владельца канала:

1. Сгруппируй посты, обсуждающие один и тот же инфоповод (одна новость в разных каналах = один кластер)
2. Для каждого кластера определи:
   - Главный заголовок новости (1 предложение, по-русски)
   - Краткое описание сути (1-3 предложения)
   - Сколько каналов запостили
   - Сумма просмотров у всех постов кластера
   - Ссылка на ЛУЧШИЙ пост кластера (максимум просмотров + самый ранний = оригинал)
3. Отранжируй кластеры по **trending score** = (кол-во каналов × суммарные просмотры). Самое горячее — сверху.
4. Выкинь не-новостные посты (реклама собственных услуг, личные мысли без новостной ценности, рерайт совсем старых новостей).
5. Покажи ВСЕ оставшиеся инфоповоды (не режь, даже если их много).

**Формат вывода** — Telegram-совместимый Markdown:

- Используй **жирный** для заголовков
- Используй _курсив_ для акцентов в описании
- **Заголовок новости должен быть гиперссылкой на лучший пост** — синтаксис `[**Заголовок**](url)` (так в Telegram он станет синей жирной кликабельной ссылкой)
- Разделяй пункты пустыми строками для читаемости
- Используй эмодзи в начале каждого пункта для визуальной категоризации (🚀 запуски, 💰 инвестиции, 🤖 новые модели, 🎨 контент-генерация, 🛠 инструменты, 📊 аналитика/рынок, 💼 бизнес/менеджмент, 🐛 баги/инциденты)

**Структура ответа:**

```
🌅 **AI-дайджест __DATE__** · N инфоповодов · __N_POSTS__ постов из __N_CHANNELS__ каналов

━━━━━━━━━━━━━━━━━━━━

1. 🚀 [**Заголовок с гиперссылкой**](https://t.me/channel/msg_id)
   📊 {X} каналов · {Y}K просмотров
   _Краткое описание сути новости в 1-3 предложениях._

2. 🤖 [**Следующий заголовок**](https://t.me/channel/msg_id)
   📊 ...
   _..._

... и так далее для всех инфоповодов ...
```

**Важно:**
- Не добавляй никаких своих комментариев до или после дайджеста
- Не используй блоки ``` — просто текст markdown
- Все ссылки должны быть реальными из поля "url" постов
- Заголовки пиши по-русски, кратко, ёмко, по делу
- Если каналов только 1 — всё равно указывай "📊 1 канал"

**ВХОДНЫЕ ДАННЫЕ (JSON):**
__POSTS_JSON__
"""


def get_posts_for_digest(conn, hours_back: int = 24) -> list[dict]:
    """Загружает посты за последние N часов + join с каналами."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
    rows = conn.execute("""
        SELECT
            p.msg_id, p.channel_id, c.username AS channel_username, c.title AS channel_title,
            p.date, p.text, p.media_type, p.views, p.forwards, p.reactions, p.url
        FROM posts p
        JOIN channels c ON c.channel_id = p.channel_id
        WHERE p.date >= ?
          AND (p.text IS NOT NULL AND LENGTH(TRIM(p.text)) >= 20)
        ORDER BY p.views DESC, p.date DESC
    """, (since,)).fetchall()

    posts = []
    for r in rows:
        posts.append({
            "id": f"{r['channel_id']}:{r['msg_id']}",
            "channel": f"@{r['channel_username']}" if r['channel_username'] else r['channel_title'],
            "channel_title": r['channel_title'],
            "date": r['date'],
            "text": (r['text'] or "").strip()[:1500],  # обрезаем гигантские посты
            "media": r['media_type'],
            "views": r['views'] or 0,
            "forwards": r['forwards'] or 0,
            "reactions": r['reactions'] or 0,
            "url": r['url'],
        })
    return posts


def build_prompt(posts: list[dict], digest_date: str) -> str:
    channels = len({p['channel'] for p in posts})
    # компактный JSON — экономим токены
    compact_posts = [
        {
            "ch": p['channel'],
            "d": p['date'],
            "v": p['views'],
            "f": p['forwards'],
            "r": p['reactions'],
            "m": p['media'],
            "url": p['url'],
            "text": p['text'],
        }
        for p in posts
    ]
    posts_json = json.dumps(compact_posts, ensure_ascii=False, separators=(',', ':'))
    return (PROMPT_TEMPLATE
            .replace("__N_POSTS__", str(len(posts)))
            .replace("__N_CHANNELS__", str(channels))
            .replace("__DATE__", digest_date)
            .replace("__POSTS_JSON__", posts_json))


def call_claude(prompt: str, model: str = "opus", max_retries: int = 3) -> tuple[str, float]:
    """Запустить `claude -p` и вернуть ответ. Возвращает (text, duration_sec).

    При транзиентных сетевых ошибках (socket closed, ETIMEDOUT) делаем retry
    с экспоненциальным бэкоффом. Это покрывает кейсы когда Anthropic API
    отвалился на пару секунд или у нас сетевой glitch.
    """
    transient_markers = (
        "socket connection was closed",
        "ETIMEDOUT",
        "ECONNRESET",
        "fetch failed",
        "Connection error",
        "Internal Server Error",
        "Service Unavailable",
        "rate_limit",
    )
    start = time.time()
    last_err: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                ["claude", "-p", "--model", model,
                 "--no-session-persistence",
                 "--output-format", "text"],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude CLI timeout (10min) for model={model}")

        if result.returncode == 0:
            return result.stdout.strip(), time.time() - start

        # Ошибка. Проверяем транзиентная ли — если да, retry
        combined = (result.stdout or "") + " " + (result.stderr or "")
        is_transient = any(m in combined for m in transient_markers)

        last_err = RuntimeError(
            f"claude CLI failed (code={result.returncode}):\n"
            f"STDOUT: {result.stdout[:500]}\nSTDERR: {result.stderr[:500]}"
        )

        if not is_transient or attempt == max_retries:
            raise last_err

        backoff = 5 * (2 ** (attempt - 1))  # 5, 10, 20 сек
        print(f"  [retry] claude транзиентная ошибка, попытка {attempt}/{max_retries}, "
              f"жду {backoff}с…")
        time.sleep(backoff)

    raise last_err  # на всякий случай


def generate_digest(model: str = "opus", hours_back: int = 24,
                    save_to_file: bool = True) -> dict:
    """Сгенерировать дайджест. Сохраняет в БД + опционально в файл."""
    conn = connect()
    try:
        posts = get_posts_for_digest(conn, hours_back)
        if not posts:
            print("Нет постов для дайджеста!")
            return {"posts_count": 0, "content": ""}

        digest_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        prompt = build_prompt(posts, digest_date)

        print(f"Генерирую дайджест через claude --model {model}...")
        print(f"  постов на входе: {len(posts)}")
        print(f"  размер промпта: {len(prompt)} символов (~{len(prompt)//4} токенов)")

        content, duration = call_claude(prompt, model=model)

        print(f"  готово за {duration:.1f}с")
        print(f"  размер ответа: {len(content)} символов")

        # Сохраняем в файл
        if save_to_file:
            out_path = config.OUTPUT_DIR / f"digest_{digest_date}_{model}.md"
            out_path.write_text(content, encoding="utf-8")
            print(f"  файл: {out_path}")

        # Сохраняем в БД
        digest_id = save_digest(
            conn,
            digest_date=digest_date,
            model=model,
            posts_count=len(posts),
            clusters_count=None,  # узнаем потом
            content=content,
            input_tokens=len(prompt) // 4,  # грубо
            output_tokens=len(content) // 4,
            duration_sec=duration,
            recipient_id=config.DIGEST_RECIPIENT_ID,
        )

        return {
            "digest_id": digest_id,
            "posts_count": len(posts),
            "content": content,
            "model": model,
            "duration": duration,
        }

    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    model = sys.argv[1] if len(sys.argv) > 1 else "opus"
    generate_digest(model=model)
