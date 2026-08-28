CREATE TABLE IF NOT EXISTS monitoring_notification_preferences (
    monitoring_id INTEGER PRIMARY KEY,
    whatsapp_enabled INTEGER NOT NULL DEFAULT 0 CHECK (whatsapp_enabled IN (0, 1)),
    origin_label TEXT,
    destination_label TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (monitoring_id) REFERENCES monitoring_jobs(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS notification_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitoring_id INTEGER NOT NULL,
    detected_at TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result = 'TEM_VAGA'),
    channel TEXT NOT NULL,
    status INTEGER NOT NULL DEFAULT 0 CHECK (status IN (0, 1, 2)),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    message TEXT NOT NULL,
    error_message TEXT,
    external_message_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (monitoring_id) REFERENCES monitoring_jobs(id) ON DELETE RESTRICT,
    UNIQUE (monitoring_id, detected_at, channel)
);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_monitoring_created
    ON notification_deliveries (monitoring_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_notification_deliveries_status
    ON notification_deliveries (status);
