CREATE TABLE IF NOT EXISTS push_subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    endpoint TEXT NOT NULL UNIQUE,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    user_agent TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    last_success_at TEXT,
    last_failure_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monitoring_push_subscriptions (
    monitoring_id INTEGER NOT NULL,
    subscription_id INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (monitoring_id, subscription_id),
    FOREIGN KEY (monitoring_id) REFERENCES monitoring_jobs(id) ON DELETE RESTRICT,
    FOREIGN KEY (subscription_id) REFERENCES push_subscriptions(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_device_active
    ON push_subscriptions (device_id, active);

CREATE INDEX IF NOT EXISTS idx_monitoring_push_active
    ON monitoring_push_subscriptions (monitoring_id, active);
