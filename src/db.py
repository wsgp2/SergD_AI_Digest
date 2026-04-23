"""Работа с SQLite базой."""
import sqlite3
from datetime import datetime, timezone
from . import config


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    if config.SCHEMA_PATH.exists():
        conn.executescript(config.SCHEMA_PATH.read_text())
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_channel(conn, channel_id: int, username: str, title: str,
                   access_hash: int | None = None):
    conn.execute("""
        INSERT INTO channels (channel_id, username, title, access_hash, added_at, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            username = excluded.username,
            title = excluded.title,
            access_hash = COALESCE(excluded.access_hash, channels.access_hash),
            last_seen = excluded.last_seen
    """, (channel_id, username, title, access_hash, now_iso(), now_iso()))


def upsert_post(conn, msg_id, channel_id, date, text, media_type,
                views, forwards, reactions, url, raw_json=None):
    conn.execute("""
        INSERT INTO posts (msg_id, channel_id, date, text, media_type,
                          views, forwards, reactions, url, raw_json,
                          collected_at, views_updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(msg_id, channel_id) DO UPDATE SET
            text = COALESCE(excluded.text, posts.text),
            views = MAX(excluded.views, posts.views),
            forwards = MAX(excluded.forwards, posts.forwards),
            reactions = MAX(excluded.reactions, posts.reactions),
            views_updated_at = excluded.views_updated_at
    """, (msg_id, channel_id, date, text, media_type,
          views or 0, forwards or 0, reactions or 0,
          url, raw_json, now_iso(), now_iso()))


def save_digest(conn, digest_date, model, posts_count, clusters_count,
                content, input_tokens, output_tokens, duration_sec,
                recipient_id=None, sent_at=None):
    cur = conn.execute("""
        INSERT INTO digests (digest_date, model, posts_count, clusters_count,
                            content, input_tokens, output_tokens, duration_sec,
                            generated_at, recipient_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (digest_date, model, posts_count, clusters_count,
          content, input_tokens, output_tokens, duration_sec,
          now_iso(), recipient_id, sent_at))
    conn.commit()
    return cur.lastrowid


def mark_digest_sent(conn, digest_id):
    conn.execute("UPDATE digests SET sent_at = ? WHERE id = ?",
                 (now_iso(), digest_id))
    conn.commit()


def log_run(conn, stage, status, details=None, duration_sec=None):
    conn.execute("""
        INSERT INTO run_logs (run_at, stage, status, details, duration_sec)
        VALUES (?, ?, ?, ?, ?)
    """, (now_iso(), stage, status, details, duration_sec))
    conn.commit()
