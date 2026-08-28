CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT,
    status INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    is_legacy INTEGER NOT NULL DEFAULT 0 CHECK (is_legacy IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT OR IGNORE INTO users (
    id, name, email, password_hash, status, is_legacy, created_at, updated_at
) VALUES (
    1,
    'Dados anteriores à Fase 6',
    'legacy-owner@efvm.local',
    NULL,
    0,
    1,
    datetime('now'),
    datetime('now')
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_token TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT
);

ALTER TABLE monitoring_jobs ADD COLUMN user_id INTEGER
    REFERENCES users(id) ON DELETE RESTRICT;

UPDATE monitoring_jobs
SET user_id = 1
WHERE user_id IS NULL;

ALTER TABLE push_subscriptions ADD COLUMN user_id INTEGER
    REFERENCES users(id) ON DELETE RESTRICT;

UPDATE push_subscriptions
SET user_id = 1
WHERE user_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_users_status_email
    ON users (status, email);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_token_active
    ON auth_sessions (token_hash, revoked_at, expires_at);

CREATE INDEX IF NOT EXISTS idx_auth_sessions_user_active
    ON auth_sessions (user_id, revoked_at, expires_at);

CREATE INDEX IF NOT EXISTS idx_monitoring_jobs_user_visible
    ON monitoring_jobs (user_id, removed_at, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user_device
    ON push_subscriptions (user_id, device_id, active, updated_at DESC);
