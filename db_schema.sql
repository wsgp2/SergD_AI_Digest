-- ─── Каналы (источники новостей) ──────────────────────────
CREATE TABLE IF NOT EXISTS channels (
    channel_id   INTEGER PRIMARY KEY,  -- Telegram channel_id (без -100 префикса, абсолютное)
    username     TEXT UNIQUE,
    title        TEXT,
    access_hash  INTEGER,
    is_active    INTEGER DEFAULT 1,
    added_at     TEXT,
    last_seen    TEXT
);

CREATE INDEX IF NOT EXISTS idx_channels_username ON channels(username);

-- ─── Посты (сырые данные) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS posts (
    msg_id        INTEGER NOT NULL,
    channel_id    INTEGER NOT NULL,
    date          TEXT NOT NULL,        -- ISO
    text          TEXT,
    media_type    TEXT,                 -- photo/video/document/audio/poll/webpage
    views         INTEGER DEFAULT 0,
    forwards      INTEGER DEFAULT 0,
    reactions     INTEGER DEFAULT 0,
    url           TEXT,                 -- t.me/channel/msg_id
    raw_json      TEXT,                 -- fallback полный дамп
    collected_at  TEXT,
    views_updated_at TEXT,              -- когда последний раз апдейтили views

    PRIMARY KEY (msg_id, channel_id),
    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(date);
CREATE INDEX IF NOT EXISTS idx_posts_channel_date ON posts(channel_id, date);

-- ─── История дайджестов ───────────────────────────────────
CREATE TABLE IF NOT EXISTS digests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    digest_date    TEXT NOT NULL,       -- дата «за какой день» (ISO YYYY-MM-DD)
    model          TEXT NOT NULL,       -- opus/sonnet/... что использовали
    posts_count    INTEGER,             -- сколько постов было на входе
    clusters_count INTEGER,             -- сколько инфоповодов выделено
    content        TEXT NOT NULL,       -- markdown для Telegram
    input_tokens   INTEGER,
    output_tokens  INTEGER,
    duration_sec   REAL,
    generated_at   TEXT NOT NULL,
    sent_at        TEXT,                -- NULL если не отправлено
    recipient_id   INTEGER
);

CREATE INDEX IF NOT EXISTS idx_digests_date ON digests(digest_date);

-- ─── Логи (для дебага) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS run_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at       TEXT NOT NULL,
    stage        TEXT NOT NULL,  -- collect/digest/send
    status       TEXT NOT NULL,  -- ok/error
    details      TEXT,
    duration_sec REAL
);

CREATE INDEX IF NOT EXISTS idx_run_logs_date ON run_logs(run_at);
