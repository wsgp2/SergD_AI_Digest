"""Сбор постов из Telegram-каналов за последние 24 часа."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Iterable

from telethon import TelegramClient
from telethon.errors import FloodWaitError, ChannelPrivateError
from telethon.tl.functions.chatlists import (
    CheckChatlistInviteRequest,
    JoinChatlistInviteRequest,
)
from telethon.tl.types import (
    Channel, MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage,
    MessageMediaPoll, DocumentAttributeAudio, DocumentAttributeVideo,
    ReactionEmoji, MessageReplyHeader,
)

from . import config
from .db import connect, upsert_channel, upsert_post, log_run


def post_url(username: str, msg_id: int) -> str:
    return f"https://t.me/{username}/{msg_id}"


def media_kind(msg) -> str | None:
    if msg.media is None:
        return None
    m = msg.media
    if isinstance(m, MessageMediaPhoto):
        return "photo"
    if isinstance(m, MessageMediaWebPage):
        return "webpage"
    if isinstance(m, MessageMediaPoll):
        return "poll"
    if isinstance(m, MessageMediaDocument):
        doc = m.document
        if not doc:
            return "document"
        mime = (doc.mime_type or "").lower()
        for attr in (doc.attributes or []):
            if isinstance(attr, DocumentAttributeAudio):
                return "voice" if getattr(attr, 'voice', False) else "audio"
            if isinstance(attr, DocumentAttributeVideo):
                return "video_note" if getattr(attr, 'round_message', False) else "video"
        if "video" in mime:
            return "video"
        if "audio" in mime:
            return "audio"
        if "image" in mime or "webp" in mime:
            return "sticker" if "webp" in mime else "image"
        return "document"
    return type(m).__name__


def reaction_count(msg) -> int:
    if not getattr(msg, 'reactions', None):
        return 0
    total = 0
    for r in (msg.reactions.results or []):
        total += getattr(r, 'count', 0)
    return total


async def safe(client, coro, context=""):
    try:
        return await coro
    except FloodWaitError as e:
        print(f"  [flood-wait] {context}: {e.seconds}s")
        await asyncio.sleep(e.seconds + 1)
        return await coro


async def get_client() -> TelegramClient:
    client = TelegramClient(config.SESSION_PATH, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)
    return client


async def import_chatlist_if_needed(client: TelegramClient, slug: str) -> list:
    """Импортировать Telegram folder share (addlist ссылку) если ещё не импортирована."""
    print(f"Проверяю папку t.me/addlist/{slug}...")
    result = await client(CheckChatlistInviteRequest(slug=slug))

    # Канал может быть в `chats` (новый) или `already_chats` (уже подписан)
    new_chats = list(getattr(result, 'chats', []))
    already = list(getattr(result, 'already_chats', []))

    print(f"  новых к добавлению: {len(new_chats)}")
    print(f"  уже подписан: {len(already)}")

    if new_chats:
        print("  импортирую новые каналы в папку...")
        # peers для импорта — это InputPeer объекты из chats
        peers = [await client.get_input_entity(c) for c in new_chats]
        try:
            await client(JoinChatlistInviteRequest(
                slug=slug,
                peers=peers,
            ))
            print(f"  ✓ добавлено {len(peers)} каналов")
        except Exception as e:
            print(f"  предупреждение при импорте: {e}")

    all_chats = new_chats + already
    return [c for c in all_chats if isinstance(c, Channel)]


async def collect_channel(client: TelegramClient, conn, channel,
                          since_dt: datetime, limit_per_channel: int = 100) -> int:
    """Собрать все посты канала с момента since_dt. Возвращает число новых/обновлённых."""
    username = getattr(channel, 'username', None)
    title = getattr(channel, 'title', None) or username
    access_hash = getattr(channel, 'access_hash', None)

    upsert_channel(conn, channel.id, username, title, access_hash)

    count = 0
    try:
        async for msg in client.iter_messages(channel, limit=limit_per_channel):
            # msg.date — UTC aware. Сравниваем с UTC aware since_dt.
            if msg.date < since_dt:
                break
            if not (msg.message or msg.media):
                continue

            upsert_post(
                conn,
                msg_id=msg.id,
                channel_id=channel.id,
                date=msg.date.isoformat(),
                text=msg.message or "",
                media_type=media_kind(msg),
                views=getattr(msg, 'views', 0) or 0,
                forwards=getattr(msg, 'forwards', 0) or 0,
                reactions=reaction_count(msg),
                url=post_url(username, msg.id) if username else None,
            )
            count += 1
    except ChannelPrivateError:
        print(f"  [private] {username or title}: нет доступа")
        return 0
    except FloodWaitError as e:
        print(f"  [flood-wait] {username}: {e.seconds}s")
        await asyncio.sleep(e.seconds + 1)
        return count
    except Exception as e:
        print(f"  [error] {username}: {e}")
        return count

    conn.commit()
    return count


async def collect_all(hours_back: int = 24) -> dict:
    """Собрать посты со всех каналов (из папки CHATLIST_INVITE)."""
    client = await get_client()
    conn = connect()
    started = datetime.now(timezone.utc)
    since_dt = started - timedelta(hours=hours_back)

    try:
        channels = []
        if config.CHATLIST_INVITE:
            channels = await import_chatlist_if_needed(client, config.CHATLIST_INVITE)
        else:
            # fallback: берём каналы из БД (если уже есть)
            for row in conn.execute(
                "SELECT channel_id, username FROM channels WHERE is_active = 1"
            ):
                try:
                    ent = await client.get_entity(row["username"] or row["channel_id"])
                    channels.append(ent)
                except Exception as e:
                    print(f"  не нашёл {row['username']}: {e}")

        print(f"\nСбор постов за последние {hours_back}ч (с {since_dt.isoformat()})")
        print(f"Каналов: {len(channels)}\n")

        total_posts = 0
        stats = {}
        for ch in channels:
            username = getattr(ch, 'username', None) or str(ch.id)
            print(f"  → @{username} ({getattr(ch, 'title', '')})...")
            n = await collect_channel(client, conn, ch, since_dt)
            stats[username] = n
            total_posts += n
            print(f"    собрано: {n}")

        duration = (datetime.now(timezone.utc) - started).total_seconds()
        print(f"\nИтого: {total_posts} постов за {duration:.1f}с")
        log_run(conn, "collect", "ok",
                details=json.dumps(stats, ensure_ascii=False),
                duration_sec=duration)

        return {"total": total_posts, "by_channel": stats, "duration": duration}

    finally:
        await client.disconnect()
        conn.close()


if __name__ == "__main__":
    import sys
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    asyncio.run(collect_all(hours))
