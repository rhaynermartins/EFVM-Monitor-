"""Camada única de acesso ao banco SQLite local."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from efvm_monitor.checker import AvailabilityStatus
from efvm_monitor.config import Settings


class MonitorStatus(IntEnum):
    PAUSED = 0
    ACTIVE = 1


@dataclass(frozen=True, slots=True)
class PersistedMonitor:
    id: int
    origin: str
    destination: str
    travel_date: date
    travel_class: str | None
    passengers: int
    interval_seconds: int
    status: MonitorStatus
    last_result: str | None
    last_message: str | None
    last_checked_at: str | None
    availability_changed_at: str | None
    created_at: str
    updated_at: str

    @property
    def active(self) -> bool:
        return self.status is MonitorStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["travel_date"] = self.travel_date.isoformat()
        data["status"] = int(self.status)
        data["active"] = self.active
        return data

    def to_settings(
        self,
        *,
        timeout_seconds: int,
        log_level: str,
        base_url: str,
        railway_code: str,
        alert_webhook_url: str | None,
    ) -> Settings:
        return Settings.for_query(
            origin=self.origin,
            destination=self.destination,
            travel_date=self.travel_date,
            travel_class=self.travel_class or "Econômica",
            passengers=self.passengers,
            check_interval_seconds=self.interval_seconds,
            timeout_seconds=timeout_seconds,
            log_level=log_level,
            base_url=base_url,
            railway_code=railway_code,
            alert_webhook_url=alert_webhook_url,
        )


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: int
    monitoring_id: int
    result: str
    message: str
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MonitoringRepository:
    """Persiste configurações e verificações sem expor SQL às outras camadas."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.migrations_directory = Path(__file__).parent / "migrations"

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration_path in sorted(self.migrations_directory.glob("*.sql")):
                version = migration_path.stem
                if version in applied:
                    continue
                connection.executescript(migration_path.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (version, self._now()),
                )
            connection.commit()

    def create_monitor(
        self,
        settings: Settings,
        status: MonitorStatus = MonitorStatus.ACTIVE,
    ) -> PersistedMonitor:
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO monitoring_jobs (
                    origin, destination, travel_date, travel_class, passengers,
                    interval_seconds, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    settings.origin,
                    settings.destination,
                    settings.travel_date.isoformat(),
                    settings.travel_class,
                    settings.passengers,
                    settings.check_interval_seconds,
                    int(status),
                    now,
                    now,
                ),
            )
            monitoring_id = int(cursor.lastrowid)
        monitor = self.get_monitor(monitoring_id)
        if monitor is None:
            raise RuntimeError("O monitoramento criado não foi encontrado no banco local.")
        return monitor

    def get_monitor(self, monitoring_id: int) -> PersistedMonitor | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM monitoring_jobs WHERE id = ?",
                (monitoring_id,),
            ).fetchone()
        return self._monitor_from_row(row) if row is not None else None

    def latest_active_monitor(self) -> PersistedMonitor | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM monitoring_jobs
                WHERE status = ?
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (int(MonitorStatus.ACTIVE),),
            ).fetchone()
        return self._monitor_from_row(row) if row is not None else None

    def latest_monitor(self) -> PersistedMonitor | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM monitoring_jobs
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
        return self._monitor_from_row(row) if row is not None else None

    def list_monitors(self) -> list[PersistedMonitor]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM monitoring_jobs ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return [self._monitor_from_row(row) for row in rows]

    def set_status(self, monitoring_id: int, status: MonitorStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE monitoring_jobs SET status = ?, updated_at = ? WHERE id = ?",
                (int(status), self._now(), monitoring_id),
            )

    def record_check(
        self,
        monitoring_id: int,
        result: AvailabilityStatus,
        message: str,
        checked_at: str,
    ) -> None:
        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT last_result, availability_changed_at
                FROM monitoring_jobs
                WHERE id = ?
                """,
                (monitoring_id,),
            ).fetchone()
            if current is None:
                raise LookupError(f"Monitoramento {monitoring_id} não encontrado.")

            previous_result = current["last_result"]
            availability_results = {
                AvailabilityStatus.TEM_VAGA.value,
                AvailabilityStatus.SEM_VAGA.value,
            }
            availability_changed_at = current["availability_changed_at"]
            if result.value in availability_results and previous_result != result.value:
                availability_changed_at = checked_at

            connection.execute(
                """
                INSERT INTO check_history (monitoring_id, result, message, checked_at)
                VALUES (?, ?, ?, ?)
                """,
                (monitoring_id, result.value, message, checked_at),
            )
            connection.execute(
                """
                UPDATE monitoring_jobs
                SET last_result = ?, last_message = ?, last_checked_at = ?,
                    availability_changed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    result.value,
                    message,
                    checked_at,
                    availability_changed_at,
                    checked_at,
                    monitoring_id,
                ),
            )

    def history(self, monitoring_id: int, limit: int = 100) -> list[HistoryEntry]:
        safe_limit = max(1, min(limit, 1_000))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, monitoring_id, result, message, checked_at
                FROM check_history
                WHERE monitoring_id = ?
                ORDER BY checked_at DESC, id DESC
                LIMIT ?
                """,
                (monitoring_id, safe_limit),
            ).fetchall()
        return [HistoryEntry(**dict(row)) for row in rows]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _monitor_from_row(row: sqlite3.Row) -> PersistedMonitor:
        return PersistedMonitor(
            id=row["id"],
            origin=row["origin"],
            destination=row["destination"],
            travel_date=date.fromisoformat(row["travel_date"]),
            travel_class=row["travel_class"],
            passengers=row["passengers"],
            interval_seconds=row["interval_seconds"],
            status=MonitorStatus(row["status"]),
            last_result=row["last_result"],
            last_message=row["last_message"],
            last_checked_at=row["last_checked_at"],
            availability_changed_at=row["availability_changed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
