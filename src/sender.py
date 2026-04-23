"""Отправка дайджеста в Telegram через Telethon (юзер-аккаунт)."""
import asyncio
from telethon import TelegramClient
from telethon.errors import MessageTooLongError

from . import config
from .db import connect, mark_digest_sent, log_run


# Telegram hard limit — 4096 символов на сообщение
MAX_MSG_LEN = 4000


def split_markdown(text: str, max_len: int = MAX_MSG_LEN) -> list[str]:
    """Разбить длинный текст на части по границам пунктов, не ломая разметку."""
    if len(text) <= max_len:
        return [text]

    parts = []
    current = ""
    # Пытаемся разбивать по двойным переносам (между пунктами)
    blocks = text.split("\n\n")
    for block in blocks:
        if len(current) + len(block) + 2 > max_len:
            if current:
                parts.append(current.rstrip())
                current = ""
            # Если один блок огромный — режем грубо
            while len(block) > max_len:
                parts.append(block[:max_len])
                block = block[max_len:]
        current += block + "\n\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


async def send_digest(digest_id: int, content: str, recipient_id: int) -> bool:
    """Отправить готовый дайджест получателю."""
    client = TelegramClient(config.SESSION_PATH, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)

    try:
        entity = await client.get_entity(recipient_id)
        parts = split_markdown(content)

        print(f"Отправляю дайджест → {recipient_id} ({len(parts)} сообщений)")
        for i, part in enumerate(parts, 1):
            await client.send_message(
                entity, part,
                parse_mode='markdown',
                link_preview=False,  # чтобы preview первой ссылки не занимал весь экран
            )
            print(f"  [{i}/{len(parts)}] отправлено ({len(part)} симв)")
            if i < len(parts):
                await asyncio.sleep(0.5)  # не спамим

        conn = connect()
        mark_digest_sent(conn, digest_id)
        log_run(conn, "send", "ok", details=f"digest_id={digest_id}, parts={len(parts)}")
        conn.close()
        return True

    except Exception as e:
        print(f"Ошибка отправки: {e}")
        conn = connect()
        log_run(conn, "send", "error", details=f"digest_id={digest_id}: {e}")
        conn.close()
        return False

    finally:
        await client.disconnect()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        # Отправить последний сгенерированный дайджест
        conn = connect()
        row = conn.execute(
            "SELECT id, content FROM digests ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            print("Нет дайджестов в БД")
            sys.exit(1)
        asyncio.run(send_digest(row["id"], row["content"], config.DIGEST_RECIPIENT_ID))
    else:
        digest_id = int(sys.argv[1])
        conn = connect()
        row = conn.execute(
            "SELECT content, recipient_id FROM digests WHERE id = ?", (digest_id,)
        ).fetchone()
        conn.close()
        asyncio.run(send_digest(digest_id, row["content"],
                                row["recipient_id"] or config.DIGEST_RECIPIENT_ID))
