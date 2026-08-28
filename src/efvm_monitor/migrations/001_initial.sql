CREATE TABLE IF NOT EXISTS monitoring_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    travel_date TEXT NOT NULL,
    travel_class TEXT,
    passengers INTEGER NOT NULL DEFAULT 1 CHECK (passengers > 0),
    interval_seconds INTEGER NOT NULL CHECK (interval_seconds >= 60),
    status INTEGER NOT NULL DEFAULT 1 CHECK (status IN (0, 1)),
    last_result TEXT CHECK (
        last_result IS NULL OR last_result IN ('TEM_VAGA', 'SEM_VAGA', 'ERRO')
    ),
    last_message TEXT,
    last_checked_at TEXT,
    availability_changed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS check_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    monitoring_id INTEGER NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('TEM_VAGA', 'SEM_VAGA', 'ERRO')),
    message TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    FOREIGN KEY (monitoring_id) REFERENCES monitoring_jobs(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_monitoring_jobs_status
    ON monitoring_jobs (status);

CREATE INDEX IF NOT EXISTS idx_check_history_monitoring_checked
    ON check_history (monitoring_id, checked_at DESC);
