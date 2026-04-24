"""Listener-бот для подписки/отписки на AI Digest.

Отдельный процесс-сервис. Слушает команды:
  /start    — подписаться, уведомить владельца
  /stop     — отписаться, уведомить владельца
  /status   — текущий статус подписки
  /help     — список команд

Владелец (ADMIN_CHAT_ID из .env) получает уведомления:
  - Новый подписчик
  - Отписка

Подписчики хранятся в таблице `subscribers` (добавится автоматически).
"""
import json
import signal
import sys
import time
import urllib.request
import urllib.error

from . import config
from .db import connect, now_iso


ADMIN_CHAT_ID = config.DIGEST_RECIPIENT_ID  # Владелец


def _init_subscribers_table():
    """Создаёт таблицу subscribers если её нет."""
    conn = connect()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS subscribers (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            first_name    TEXT,
            subscribed_at TEXT NOT NULL,
            unsubscribed_at TEXT,
            is_active     INTEGER DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_subscribers_active ON subscribers(is_active);
    """)
    conn.commit()

    # Добавляем владельца по умолчанию, если ещё не в списке
    conn.execute("""
        INSERT OR IGNORE INTO subscribers (user_id, username, first_name,
                                           subscribed_at, is_active)
        VALUES (?, 'owner', 'Owner', ?, 1)
    """, (ADMIN_CHAT_ID, now_iso()))
    conn.commit()
    conn.close()


def tg_request(method: str, **params) -> dict:
    """Вызов метода Bot API."""
    url = f"https://api.telegram.org/bot{config.BOT_TOKEN}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return {"ok": False, "error": f"HTTP {e.code}: {body[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def send_message(chat_id: int, text: str, parse_mode: str = "HTML") -> bool:
    r = tg_request("sendMessage",
                   chat_id=chat_id,
                   text=text,
                   parse_mode=parse_mode,
                   disable_web_page_preview=True)
    if not r.get("ok"):
        print(f"[send] fail to {chat_id}: {r.get('error', r)}")
    return bool(r.get("ok"))


def notify_admin(text: str):
    """Уведомление владельцу."""
    send_message(ADMIN_CHAT_ID, f"🔔 <b>AI Digest</b>\n\n{text}")


def handle_start(user_id: int, username: str | None, first_name: str | None):
    """Подписка."""
    conn = connect()
    row = conn.execute(
        "SELECT is_active FROM subscribers WHERE user_id = ?", (user_id,)
    ).fetchone()

    if row and row["is_active"] == 1:
        # Уже подписан
        send_message(user_id,
            "✅ Вы уже подписаны на утренний AI-дайджест!\n\n"
            "Каждый день в 08:00 Бали (04:00 UTC) вы получаете "
            "свежую сводку AI-новостей.\n\n"
            "/stop — отписаться\n"
            "/status — статус\n"
            "/help — помощь")
        conn.close()
        return

    if row:
        # Был отписан — реактивируем
        conn.execute("""
            UPDATE subscribers SET is_active = 1, subscribed_at = ?,
                                   unsubscribed_at = NULL,
                                   username = ?, first_name = ?
            WHERE user_id = ?
        """, (now_iso(), username, first_name, user_id))
        action = "переподписался"
    else:
        # Новый
        conn.execute("""
            INSERT INTO subscribers (user_id, username, first_name,
                                     subscribed_at, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, (user_id, username, first_name, now_iso()))
        action = "подписался"

    conn.commit()
    # Считаем всего активных
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM subscribers WHERE is_active = 1"
    ).fetchone()["n"]
    conn.close()

    # Приветствие
    send_message(user_id,
        "🌅 <b>Добро пожаловать в AI Digest!</b>\n\n"
        "Каждое утро в 08:00 Бали (04:00 UTC) вы будете получать "
        "свежий дайджест главных AI-новостей из 25+ каналов.\n\n"
        "Команды:\n"
        "/stop — отписаться\n"
        "/status — статус подписки\n"
        "/help — справка\n\n"
        "Ждите первое сообщение завтра утром!")

    # Уведомление владельцу (кроме самого владельца)
    if user_id != ADMIN_CHAT_ID:
        uname = f"@{username}" if username else "(без username)"
        name = first_name or "Аноним"
        notify_admin(
            f"➕ Новый подписчик {action}\n\n"
            f"<b>{name}</b> {uname}\n"
            f"ID: <code>{user_id}</code>\n\n"
            f"Всего активных: <b>{total}</b>")


def handle_stop(user_id: int, username: str | None, first_name: str | None):
    """Отписка."""
    conn = connect()
    row = conn.execute(
        "SELECT is_active FROM subscribers WHERE user_id = ?", (user_id,)
    ).fetchone()

    if not row or row["is_active"] == 0:
        send_message(user_id,
            "Вы и так не подписаны.\n\n"
            "/start — подписаться снова")
        conn.close()
        return

    conn.execute("""
        UPDATE subscribers SET is_active = 0, unsubscribed_at = ?
        WHERE user_id = ?
    """, (now_iso(), user_id))
    conn.commit()

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM subscribers WHERE is_active = 1"
    ).fetchone()["n"]
    conn.close()

    send_message(user_id,
        "🔴 Вы отписаны от AI Digest.\n\n"
        "Передумаете — просто отправьте /start")

    if user_id != ADMIN_CHAT_ID:
        uname = f"@{username}" if username else "(без username)"
        name = first_name or "Аноним"
        notify_admin(
            f"➖ Отписка\n\n"
            f"<b>{name}</b> {uname}\n"
            f"ID: <code>{user_id}</code>\n\n"
            f"Осталось активных: <b>{total}</b>")


def handle_status(user_id: int):
    conn = connect()
    row = conn.execute("""
        SELECT is_active, subscribed_at, unsubscribed_at
        FROM subscribers WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()

    if not row:
        send_message(user_id,
            "Вы не подписаны.\n\n/start — подписаться")
        return

    if row["is_active"] == 1:
        send_message(user_id,
            f"✅ <b>Подписка активна</b>\n\n"
            f"С: {row['subscribed_at'][:10]}\n\n"
            f"/stop — отписаться")
    else:
        send_message(user_id,
            f"🔴 <b>Подписка неактивна</b>\n\n"
            f"Отписан: {row['unsubscribed_at'][:10]}\n\n"
            f"/start — возобновить")


def handle_help(user_id: int):
    send_message(user_id,
        "<b>AI Digest</b> — утренний дайджест AI-новостей\n\n"
        "Команды:\n"
        "/start — подписаться\n"
        "/stop — отписаться\n"
        "/status — статус подписки\n"
        "/help — эта справка\n\n"
        "Дайджест приходит ежедневно в 08:00 Бали (04:00 UTC).")


def handle_stats(user_id: int):
    """/stats — только для владельца."""
    if user_id != ADMIN_CHAT_ID:
        return
    conn = connect()
    active = conn.execute(
        "SELECT COUNT(*) AS n FROM subscribers WHERE is_active = 1"
    ).fetchone()["n"]
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM subscribers"
    ).fetchone()["n"]
    recent = conn.execute("""
        SELECT user_id, username, first_name, subscribed_at
        FROM subscribers WHERE is_active = 1
        ORDER BY subscribed_at DESC LIMIT 10
    """).fetchall()
    conn.close()

    lines = [f"📊 <b>Статистика подписчиков</b>\n",
             f"Активных: <b>{active}</b>",
             f"Всего зарегистрировано: <b>{total}</b>\n",
             "<b>Последние 10 подписок:</b>"]
    for r in recent:
        uname = f"@{r['username']}" if r['username'] else ""
        lines.append(f"• {r['first_name'] or '?'} {uname} — {r['subscribed_at'][:10]}")

    send_message(user_id, "\n".join(lines))


COMMANDS = {
    "/start": lambda msg: handle_start(
        msg["from"]["id"], msg["from"].get("username"), msg["from"].get("first_name")),
    "/stop": lambda msg: handle_stop(
        msg["from"]["id"], msg["from"].get("username"), msg["from"].get("first_name")),
    "/status": lambda msg: handle_status(msg["from"]["id"]),
    "/help": lambda msg: handle_help(msg["from"]["id"]),
    "/stats": lambda msg: handle_stats(msg["from"]["id"]),
}


def set_my_commands():
    """Зарегистрировать список команд в Telegram."""
    tg_request("setMyCommands", commands=[
        {"command": "start", "description": "Подписаться на AI Digest"},
        {"command": "stop", "description": "Отписаться"},
        {"command": "status", "description": "Статус подписки"},
        {"command": "help", "description": "Справка"},
    ])


def poll_loop():
    print("AI Digest Subscriber Bot запущен. Жду сообщений…")
    offset = 0
    while True:
        try:
            r = tg_request("getUpdates", offset=offset, timeout=30)
            if not r.get("ok"):
                print(f"[poll] error: {r}")
                time.sleep(5)
                continue

            for update in r.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg or "text" not in msg:
                    continue

                text = msg["text"].strip().split()[0].lower()
                handler = COMMANDS.get(text)
                if handler:
                    uid = msg["from"]["id"]
                    print(f"[{now_iso()}] {text} from {uid} "
                          f"(@{msg['from'].get('username', '?')})")
                    try:
                        handler(msg)
                    except Exception as e:
                        print(f"[handler] error: {e}")
        except Exception as e:
            print(f"[poll] exception: {e}")
            time.sleep(5)


def main():
    def _shutdown(signum, frame):
        print("Bye")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    _init_subscribers_table()
    set_my_commands()
    poll_loop()


if __name__ == "__main__":
    main()
