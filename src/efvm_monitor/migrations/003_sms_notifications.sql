CREATE TABLE IF NOT EXISTS monitoring_sms_preferences (
    monitoring_id INTEGER PRIMARY KEY,
    sms_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sms_enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (monitoring_id) REFERENCES monitoring_jobs(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS notification_delivery_metadata (
    delivery_id INTEGER PRIMARY KEY,
    provider TEXT NOT NULL,
    recipient_masked TEXT,
    event TEXT NOT NULL,
    FOREIGN KEY (delivery_id) REFERENCES notification_deliveries(id) ON DELETE RESTRICT
);
