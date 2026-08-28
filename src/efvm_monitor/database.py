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


class NotificationStatus(IntEnum):
    PENDING = 0
    SENT = 1
    FAILED = 2


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
    whatsapp_enabled: bool = False
    sms_enabled: bool = False
    origin_label: str | None = None
    destination_label: str | None = None
    removed_at: str | None = None

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
            whatsapp_enabled=self.whatsapp_enabled,
            sms_enabled=self.sms_enabled,
            origin_label=self.origin_label,
            destination_label=self.destination_label,
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


@dataclass(frozen=True, slots=True)
class NotificationDelivery:
    id: int
    monitoring_id: int
    detected_at: str
    result: str
    channel: str
    status: NotificationStatus
    attempt_count: int
    message: str
    error_message: str | None
    external_message_id: str | None
    created_at: str
    updated_at: str
    provider: str | None = None
    recipient_masked: str | None = None
    event: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = int(self.status)
        data["status_name"] = self.status.name
        return data


@dataclass(frozen=True, slots=True)
class PushSubscription:
    id: int
    device_id: str
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None
    active: bool
    last_success_at: str | None
    last_failure_at: str | None
    created_at: str
    updated_at: str

    def to_push_info(self) -> dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


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
            connection.execute(
                """
                INSERT INTO monitoring_notification_preferences (
                    monitoring_id, whatsapp_enabled, origin_label, destination_label,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    monitoring_id,
                    int(settings.whatsapp_enabled),
                    settings.origin_label,
                    settings.destination_label,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO monitoring_sms_preferences (
                    monitoring_id, sms_enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (monitoring_id, int(settings.sms_enabled), now, now),
            )
        monitor = self.get_monitor(monitoring_id)
        if monitor is None:
            raise RuntimeError("O monitoramento criado não foi encontrado no banco local.")
        return monitor

    def get_monitor(self, monitoring_id: int) -> PersistedMonitor | None:
        with self._connect() as connection:
            row = connection.execute(
                f"{self._monitor_select()} WHERE m.id = ? AND m.removed_at IS NULL",
                (monitoring_id,),
            ).fetchone()
        return self._monitor_from_row(row) if row is not None else None

    def latest_active_monitor(self) -> PersistedMonitor | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                {self._monitor_select()}
                WHERE m.status = ? AND m.removed_at IS NULL
                ORDER BY m.updated_at DESC, m.id DESC
                LIMIT 1
                """,
                (int(MonitorStatus.ACTIVE),),
            ).fetchone()
        return self._monitor_from_row(row) if row is not None else None

    def latest_monitor(self) -> PersistedMonitor | None:
        with self._connect() as connection:
            row = connection.execute(
                f"""
                {self._monitor_select()}
                WHERE m.removed_at IS NULL
                ORDER BY m.updated_at DESC, m.id DESC
                LIMIT 1
                """
            ).fetchone()
        return self._monitor_from_row(row) if row is not None else None

    def list_monitors(self) -> list[PersistedMonitor]:
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                {self._monitor_select()}
                WHERE m.removed_at IS NULL
                ORDER BY m.created_at DESC, m.id DESC
                """
            ).fetchall()
        return [self._monitor_from_row(row) for row in rows]

    def set_status(self, monitoring_id: int, status: MonitorStatus) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE monitoring_jobs
                SET status = ?, updated_at = ?
                WHERE id = ? AND removed_at IS NULL
                """,
                (int(status), self._now(), monitoring_id),
            )

    def remove_monitor(self, monitoring_id: int) -> bool:
        """Oculta um monitor sem apagar configuração, histórico ou entregas."""
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE monitoring_jobs
                SET status = ?, removed_at = ?, updated_at = ?
                WHERE id = ? AND removed_at IS NULL
                """,
                (int(MonitorStatus.PAUSED), now, now, monitoring_id),
            )
        return cursor.rowcount > 0

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
                WHERE id = ? AND removed_at IS NULL
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
                  AND removed_at IS NULL
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

    def begin_notification(
        self,
        monitoring_id: int,
        *,
        detected_at: str,
        result: AvailabilityStatus,
        channel: str,
        message: str,
        provider: str | None = None,
        recipient_masked: str | None = None,
        event: str = "DISPONIBILIDADE_ENCONTRADA",
    ) -> NotificationDelivery | None:
        """Reserva uma entrega única antes do envio para evitar alertas duplicados."""
        now = self._now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO notification_deliveries (
                    monitoring_id, detected_at, result, channel, status,
                    attempt_count, message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    monitoring_id,
                    detected_at,
                    result.value,
                    channel,
                    int(NotificationStatus.PENDING),
                    0,
                    message,
                    now,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                return None
            delivery_id = int(cursor.lastrowid)
            if provider is not None:
                connection.execute(
                    """
                    INSERT INTO notification_delivery_metadata (
                        delivery_id, provider, recipient_masked, event
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (delivery_id, provider, recipient_masked, event),
                )
        return self.get_notification(delivery_id)

    def get_notification(self, delivery_id: int) -> NotificationDelivery | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT n.*, md.provider, md.recipient_masked, md.event
                FROM notification_deliveries AS n
                LEFT JOIN notification_delivery_metadata AS md ON md.delivery_id = n.id
                WHERE n.id = ?
                """,
                (delivery_id,),
            ).fetchone()
        return self._notification_from_row(row) if row is not None else None

    def complete_notification(
        self,
        delivery_id: int,
        *,
        status: NotificationStatus,
        attempt_count: int,
        error_message: str | None = None,
        external_message_id: str | None = None,
    ) -> None:
        if status is NotificationStatus.PENDING:
            raise ValueError("Uma entrega concluída deve estar enviada ou com falha.")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE notification_deliveries
                SET status = ?, attempt_count = ?, error_message = ?,
                    external_message_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    int(status),
                    attempt_count,
                    error_message,
                    external_message_id,
                    self._now(),
                    delivery_id,
                ),
            )

    def notification_history(
        self,
        monitoring_id: int,
        limit: int = 100,
    ) -> list[NotificationDelivery]:
        safe_limit = max(1, min(limit, 1_000))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT n.*, md.provider, md.recipient_masked, md.event
                FROM notification_deliveries AS n
                LEFT JOIN notification_delivery_metadata AS md ON md.delivery_id = n.id
                WHERE n.monitoring_id = ?
                ORDER BY n.created_at DESC, n.id DESC
                LIMIT ?
                """,
                (monitoring_id, safe_limit),
            ).fetchall()
        return [self._notification_from_row(row) for row in rows]

    def upsert_push_subscription(
        self,
        *,
        device_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        user_agent: str | None,
    ) -> PushSubscription:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO push_subscriptions (
                    device_id, endpoint, p256dh, auth, user_agent, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(endpoint) DO UPDATE SET
                    device_id = excluded.device_id,
                    p256dh = excluded.p256dh,
                    auth = excluded.auth,
                    user_agent = excluded.user_agent,
                    active = 1,
                    updated_at = excluded.updated_at
                """,
                (device_id, endpoint, p256dh, auth, user_agent, now, now),
            )
            row = connection.execute(
                "SELECT * FROM push_subscriptions WHERE endpoint = ?",
                (endpoint,),
            ).fetchone()
        if row is None:
            raise RuntimeError("A inscrição Web Push não foi salva.")
        return self._push_subscription_from_row(row)

    def get_push_subscription_for_device(
        self,
        device_id: str,
    ) -> PushSubscription | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM push_subscriptions
                WHERE device_id = ? AND active = 1
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """,
                (device_id,),
            ).fetchone()
        return self._push_subscription_from_row(row) if row is not None else None

    def list_push_subscriptions(self, monitoring_id: int) -> list[PushSubscription]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT s.*
                FROM push_subscriptions AS s
                INNER JOIN monitoring_push_subscriptions AS link
                    ON link.subscription_id = s.id
                WHERE link.monitoring_id = ?
                  AND link.active = 1
                  AND s.active = 1
                ORDER BY s.created_at, s.id
                """,
                (monitoring_id,),
            ).fetchall()
        return [self._push_subscription_from_row(row) for row in rows]

    def link_push_subscription(
        self,
        monitoring_id: int,
        subscription_id: int,
    ) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO monitoring_push_subscriptions (
                    monitoring_id, subscription_id, active, created_at, updated_at
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(monitoring_id, subscription_id) DO UPDATE SET
                    active = 1,
                    updated_at = excluded.updated_at
                """,
                (monitoring_id, subscription_id, now, now),
            )

    def deactivate_push_subscription(self, subscription_id: int) -> None:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE push_subscriptions
                SET active = 0, updated_at = ?
                WHERE id = ?
                """,
                (now, subscription_id),
            )
            connection.execute(
                """
                UPDATE monitoring_push_subscriptions
                SET active = 0, updated_at = ?
                WHERE subscription_id = ?
                """,
                (now, subscription_id),
            )

    def unsubscribe_push(self, *, device_id: str, endpoint: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM push_subscriptions
                WHERE device_id = ? AND endpoint = ? AND active = 1
                """,
                (device_id, endpoint),
            ).fetchone()
        if row is None:
            return False
        self.deactivate_push_subscription(int(row["id"]))
        return True

    def mark_push_success(self, subscription_id: int, sent_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE push_subscriptions
                SET last_success_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (sent_at, sent_at, subscription_id),
            )

    def mark_push_failure(
        self,
        subscription_id: int,
        failed_at: str,
        *,
        deactivate: bool = False,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE push_subscriptions
                SET last_failure_at = ?, active = ?, updated_at = ?
                WHERE id = ?
                """,
                (failed_at, int(not deactivate), failed_at, subscription_id),
            )
            if deactivate:
                connection.execute(
                    """
                    UPDATE monitoring_push_subscriptions
                    SET active = 0, updated_at = ?
                    WHERE subscription_id = ?
                    """,
                    (failed_at, subscription_id),
                )

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
            whatsapp_enabled=bool(row["whatsapp_enabled"]),
            sms_enabled=bool(row["sms_enabled"]),
            origin_label=row["origin_label"],
            destination_label=row["destination_label"],
            removed_at=row["removed_at"],
        )

    @staticmethod
    def _notification_from_row(row: sqlite3.Row) -> NotificationDelivery:
        data = dict(row)
        data["status"] = NotificationStatus(data["status"])
        return NotificationDelivery(**data)

    @staticmethod
    def _push_subscription_from_row(row: sqlite3.Row) -> PushSubscription:
        data = dict(row)
        data["active"] = bool(data["active"])
        return PushSubscription(**data)

    @staticmethod
    def _monitor_select() -> str:
        return """
            SELECT m.*,
                   COALESCE(p.whatsapp_enabled, 0) AS whatsapp_enabled,
                   COALESCE(s.sms_enabled, 0) AS sms_enabled,
                   p.origin_label,
                   p.destination_label
            FROM monitoring_jobs AS m
            LEFT JOIN monitoring_notification_preferences AS p
                ON p.monitoring_id = m.id
            LEFT JOIN monitoring_sms_preferences AS s
                ON s.monitoring_id = m.id
        """

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")
