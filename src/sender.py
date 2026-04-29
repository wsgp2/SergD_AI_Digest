"""Отправка дайджеста в Telegram.

Основной канал — Telegram Bot API (сообщения приходят от бота, красиво).
Fallback — Telethon userbot (если бот не настроен или получатель не сделал /start).
"""
import asyncio
import html
import json
import re
import urllib.request
import urllib.error

from telethon import TelegramClient

from . import config
from .db import connect, mark_digest_sent, log_run, now_iso


# Telegram hard limit — 4096 символов на сообщение
MAX_MSG_LEN = 4000


def markdown_to_telegram_html(text: str) -> str:
    """Конвертируем CommonMark-разметку (Claude её выдаёт) в Telegram HTML.

    Telegram Markdown-режимы капризные (двойные `**` не работают в legacy,
    MarkdownV2 требует экранирования десятка символов). HTML-режим
    предсказуемее — из спецсимволов нужны только &lt; &gt; &amp;.
    """
    # 1. Экранируем HTML-спецсимволы в исходном тексте
    text = html.escape(text, quote=False)

    # 2. Ссылки [label](url) → <a href="url">label</a>
    #    Делаем первыми, т.к. label может содержать **bold**
    def _link(m):
        label = m.group(1)
        url = m.group(2)
        return f'<a href="{url}">{label}</a>'
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', _link, text)

    # 3. Bold **text** → <b>text</b>
    text = re.sub(r'\*\*([^*\n]+?)\*\*', r'<b>\1</b>', text)

    # 4. Bold-альтернатива *text* → <b>text</b>
    #    Только если звёздочки не из ненастоящих кейсов (e.g. матем. умножение)
    text = re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'<b>\1</b>', text)

    # 5. Italic _text_ → <i>text</i>
    #    Аналогично — не захватываем подчёркивания внутри слов типа `var_name`
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'<i>\1</i>', text)

    # 6. Inline code `text` → <code>text</code>
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)

    return text


def split_markdown(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Разбить длинный текст на части по границам пунктов, не ломая разметку."""
    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""
    for block in text.split("\n\n"):
        if len(current) + len(block) + 2 > max_len:
            if current:
                parts.append(current.rstrip())
                current = ""
            while len(block) > max_len:
                parts.append(block[:max_len])
                block = block[max_len:]
        current += block + "\n\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


# ─── Bot API ─────────────────────────────────────────────

def send_via_bot(content: str, recipient_id: int, bot_token: str) -> tuple[bool, str]:
    """Отправить через Telegram Bot API. Возвращает (ok, error_details)."""
    # Claude отдаёт CommonMark (**жирный**, _курсив_), Telegram Bot API
    # корректнее рендерит HTML — конвертируем перед отправкой.
    html_content = markdown_to_telegram_html(content)
    parts = split_markdown(html_content)
    print(f"Отправляю через бота → {recipient_id} ({len(parts)} сообщений)")

    for i, part in enumerate(parts, 1):
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        body = {
            "chat_id": recipient_id,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
            if not result.get("ok"):
                return False, f"part {i}: {result}"
            print(f"  [{i}/{len(parts)}] ok ({len(part)} симв)")
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode() if e.fp else ""
            return False, f"HTTP {e.code} on part {i}: {body_txt[:300]}"
        except Exception as e:
            return False, f"Error on part {i}: {e}"

    return True, ""


# ─── Telethon userbot (fallback) ─────────────────────────

async def send_via_telethon(content: str, recipient_id: int) -> tuple[bool, str]:
    """Fallback: отправить с userbot-аккаунта (от имени Сергея)."""
    client = TelegramClient(config.SESSION_PATH, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)

    try:
        entity = await client.get_entity(recipient_id)
        html_content = markdown_to_telegram_html(content)
        parts = split_markdown(html_content)
        print(f"[fallback] Отправляю через Telethon → {recipient_id} ({len(parts)} сообщений)")

        for i, part in enumerate(parts, 1):
            await client.send_message(
                entity, part,
                parse_mode='html',
                link_preview=False,
            )
            print(f"  [{i}/{len(parts)}] отправлено")
            if i < len(parts):
                await asyncio.sleep(0.5)
        return True, ""

    except Exception as e:
        return False, str(e)
    finally:
        await client.disconnect()


# ─── Универсальный send ──────────────────────────────────

def _auto_unsubscribe(user_id: int, reason: str):
    """Автоматическая отписка пользователя (заблокировал бота / удалил аккаунт / т.п.).

    Не падает если таблицы subscribers ещё нет.
    """
    try:
        conn = connect()
        conn.execute("""
            UPDATE subscribers
            SET is_active = 0, unsubscribed_at = ?
            WHERE user_id = ? AND is_active = 1
        """, (now_iso(), user_id))
        conn.commit()
        conn.close()
        print(f"  [auto-unsubscribe] {user_id} ({reason})")
    except Exception as e:
        print(f"  [auto-unsubscribe] failed for {user_id}: {e}")


def _is_user_gone(error_str: str) -> bool:
    """Проверяет, означает ли ошибка что пользователь больше недоступен."""
    markers = (
        "bot was blocked by the user",
        "user is deactivated",
        "chat not found",
        "USER_IS_BLOCKED",
        "PEER_ID_INVALID",
    )
    return any(m in error_str for m in markers)


async def send_digest(digest_id: int, content: str, recipient_id: int,
                      prefer_bot: bool = True) -> bool:
    """Отправить дайджест одному получателю. Сначала пробуем бот, fallback на Telethon."""
    sent = False
    error = None
    delivery = None

    if prefer_bot and config.BOT_TOKEN:
        ok, err = send_via_bot(content, recipient_id, config.BOT_TOKEN)
        if ok:
            sent = True
            delivery = "bot"
        else:
            # Если пользователь заблокировал бота — автоматически отписываем
            if _is_user_gone(err):
                print(f"Пользователь {recipient_id} недоступен: {err[:100]}")
                _auto_unsubscribe(recipient_id, err[:80])
                conn = connect()
                log_run(conn, "send", "auto_unsubscribe",
                        details=f"digest_id={digest_id}, recipient={recipient_id}: {err[:200]}")
                conn.close()
                return False
            print(f"Bot API не сработал: {err}")
            print("Переключаюсь на Telethon...")
            error = err

    if not sent:
        ok, err = await send_via_telethon(content, recipient_id)
        if ok:
            sent = True
            delivery = "telethon"
        else:
            error = (error + " | " if error else "") + err

    conn = connect()
    if sent:
        mark_digest_sent(conn, digest_id)
        log_run(conn, "send", "ok",
                details=f"digest_id={digest_id}, recipient={recipient_id}, via={delivery}")
    else:
        log_run(conn, "send", "error",
                details=f"digest_id={digest_id}, recipient={recipient_id}: {error}")
    conn.close()
    return sent


async def send_to_all_subscribers(digest_id: int, content: str) -> dict:
    """Отправить дайджест всем активным подписчикам.

    Возвращает статистику {sent, failed, recipients}.
    """
    # Читаем активных подписчиков
    conn = connect()
    # Если таблицы нет — fallback на одного владельца
    try:
        rows = conn.execute(
            "SELECT user_id, username, first_name FROM subscribers WHERE is_active = 1"
        ).fetchall()
        recipients = [r["user_id"] for r in rows]
    except Exception:
        # Таблицы subscribers ещё нет — работаем со старой логикой
        recipients = [config.DIGEST_RECIPIENT_ID]
    conn.close()

    if not recipients:
        # Защита от пустого списка — всё равно шлём владельцу
        recipients = [config.DIGEST_RECIPIENT_ID]

    print(f"\nРассылка {len(recipients)} подписчикам…")
    sent_count = 0
    failed_count = 0

    for i, recipient_id in enumerate(recipients, 1):
        print(f"\n[{i}/{len(recipients)}] → {recipient_id}")
        try:
            ok = await send_digest(digest_id, content, recipient_id,
                                   prefer_bot=True)
            if ok:
                sent_count += 1
            else:
                failed_count += 1
        except Exception as e:
            print(f"  ошибка: {e}")
            failed_count += 1

    print(f"\nРезультат: отправлено {sent_count}/{len(recipients)}, "
          f"ошибок {failed_count}")
    return {
        "sent": sent_count,
        "failed": failed_count,
        "recipients": len(recipients),
    }


if __name__ == "__main__":
    import sys
    conn = connect()
    if len(sys.argv) < 2:
        row = conn.execute(
            "SELECT id, content, recipient_id FROM digests ORDER BY id DESC LIMIT 1"
        ).fetchone()
    else:
        digest_id = int(sys.argv[1])
        row = conn.execute(
            "SELECT id, content, recipient_id FROM digests WHERE id = ?",
            (digest_id,)
        ).fetchone()
    conn.close()

    if not row:
        print("Дайджест не найден")
        sys.exit(1)

    asyncio.run(send_digest(
        row["id"], row["content"],
        row["recipient_id"] or config.DIGEST_RECIPIENT_ID,
    ))
