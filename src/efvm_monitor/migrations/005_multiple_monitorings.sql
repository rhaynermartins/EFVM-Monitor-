ALTER TABLE monitoring_jobs ADD COLUMN removed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_monitoring_jobs_visible_status
    ON monitoring_jobs (removed_at, status, updated_at DESC);
