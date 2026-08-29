"""Gerenciamento concorrente de monitoramentos independentes."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from efvm_monitor.checker import EFVMClient
from efvm_monitor.config import Settings
from efvm_monitor.database import (
    LEGACY_USER_ID,
    MonitoringRepository,
    MonitorStatus,
    PersistedMonitor,
)
from efvm_monitor.monitor import MonitorAlreadyRunning, MonitorService, MonitorSnapshot

LOGGER = logging.getLogger(__name__)


class MonitoringNotFound(LookupError):
    """Indica que o ID não pertence a um monitoramento visível."""


class MonitoringLimitReached(RuntimeError):
    """Indica que o usuário atingiu o limite defensivo de monitoramentos."""


class MonitoringManager:
    """Mantém um executor isolado por ID e compartilha apenas a persistência."""

    def __init__(
        self,
        repository: MonitoringRepository,
        *,
        client_factory: Callable[[Settings], EFVMClient] = EFVMClient,
        notifier: Callable[[Settings, Any, int | None, str], None] | None = None,
        max_monitors_per_user: int = 10,
    ) -> None:
        if max_monitors_per_user < 1:
            raise ValueError("O limite de monitoramentos deve ser positivo.")
        self.repository = repository
        self._client_factory = client_factory
        self._notifier = notifier
        self._max_monitors_per_user = max_monitors_per_user
        self._services: dict[int, MonitorService] = {}
        self._lock = threading.RLock()

    def create(
        self,
        settings: Settings,
        user_id: int = LEGACY_USER_ID,
    ) -> MonitorSnapshot:
        with self._lock:
            monitor_count = len(self.repository.list_monitors(user_id=user_id))
            if monitor_count >= self._max_monitors_per_user:
                raise MonitoringLimitReached(
                    f"O limite de {self._max_monitors_per_user} monitoramentos foi atingido."
                )
            service = self._new_service()
            snapshot = service.start(settings, user_id=user_id)
            if snapshot.monitoring_id is None:
                service.shutdown()
                raise RuntimeError("O monitoramento não recebeu um ID persistente.")
            self._services[snapshot.monitoring_id] = service
        LOGGER.info(
            "Monitoramento criado.",
            extra={
                "event": "monitor_created",
                "monitoring_id": snapshot.monitoring_id,
                "user_id": user_id,
            },
        )
        return snapshot

    def list(self, user_id: int = LEGACY_USER_ID) -> list[MonitorSnapshot]:
        monitors = self.repository.list_monitors(user_id=user_id)
        with self._lock:
            services = dict(self._services)
        return [
            services[monitor.id].snapshot()
            if monitor.id in services
            else self._snapshot_from_monitor(monitor)
            for monitor in monitors
        ]

    def snapshot(
        self,
        monitoring_id: int,
        user_id: int = LEGACY_USER_ID,
    ) -> MonitorSnapshot:
        monitor = self._require_monitor(monitoring_id, user_id)
        with self._lock:
            service = self._services.get(monitoring_id)
        return service.snapshot() if service is not None else self._snapshot_from_monitor(monitor)

    def pause(
        self,
        monitoring_id: int,
        user_id: int = LEGACY_USER_ID,
    ) -> MonitorSnapshot:
        self._require_monitor(monitoring_id, user_id)
        with self._lock:
            service = self._services.pop(monitoring_id, None)
        if service is not None:
            service.stop()
            service.shutdown()
        else:
            self.repository.set_status(monitoring_id, MonitorStatus.PAUSED)
        return self.snapshot(monitoring_id, user_id=user_id)

    def resume(
        self,
        monitoring_id: int,
        settings: Settings,
        user_id: int = LEGACY_USER_ID,
    ) -> MonitorSnapshot:
        monitor = self._require_monitor(monitoring_id, user_id)
        with self._lock:
            current = self._services.get(monitoring_id)
            if current is not None and current.snapshot().running:
                raise MonitorAlreadyRunning(f"O monitoramento {monitoring_id} já está ativo.")
            self._services.pop(monitoring_id, None)

        service = self._new_service()
        snapshot = service.start(settings, monitoring_id=monitor.id, user_id=user_id)
        with self._lock:
            self._services[monitoring_id] = service
        return snapshot

    def remove(self, monitoring_id: int, user_id: int = LEGACY_USER_ID) -> None:
        self._require_monitor(monitoring_id, user_id)
        with self._lock:
            service = self._services.pop(monitoring_id, None)
        if service is not None:
            service.stop()
            service.shutdown()
        if not self.repository.remove_monitor(monitoring_id):
            raise MonitoringNotFound(f"Monitoramento {monitoring_id} não encontrado.")

    def history(
        self,
        monitoring_id: int,
        limit: int = 100,
        user_id: int = LEGACY_USER_ID,
    ) -> list[dict[str, Any]]:
        self._require_monitor(monitoring_id, user_id)
        return [
            entry.to_dict()
            for entry in self.repository.history(monitoring_id, limit, user_id=user_id)
        ]

    def restore_all(
        self,
        settings_factory: Callable[[PersistedMonitor], Settings],
    ) -> list[MonitorSnapshot]:
        restored: list[MonitorSnapshot] = []
        for monitor in self.repository.list_all_monitors():
            if not monitor.active:
                restored.append(self._snapshot_from_monitor(monitor))
                continue
            try:
                settings = settings_factory(monitor)
                restored.append(
                    self.resume(monitor.id, settings, user_id=monitor.user_id)
                )
            except Exception as exc:
                LOGGER.error(
                    "Monitoramento não pôde ser retomado: %s",
                    exc,
                    extra={
                        "event": "monitor_restore_failed",
                        "monitoring_id": monitor.id,
                        "user_id": monitor.user_id,
                    },
                )
                self.repository.set_status(monitor.id, MonitorStatus.PAUSED)
                recovered = self.repository.get_monitor(
                    monitor.id,
                    user_id=monitor.user_id,
                )
                if recovered is not None:
                    restored.append(self._snapshot_from_monitor(recovered, message=str(exc)))
        return restored

    def operational_status(self) -> dict[str, Any]:
        """Resume a correspondência entre monitores ativos e workers vivos."""
        active_ids = {
            monitor.id for monitor in self.repository.list_all_monitors() if monitor.active
        }
        with self._lock:
            registered_workers = len(self._services)
            running_ids = {
                monitoring_id
                for monitoring_id, service in self._services.items()
                if service.snapshot().running
            }
        stalled_ids = sorted(active_ids - running_ids)
        orphaned_ids = sorted(running_ids - active_ids)
        healthy = not stalled_ids and not orphaned_ids
        return {
            "status": "ok" if healthy else "degraded",
            "active_monitors": len(active_ids),
            "registered_workers": registered_workers,
            "running_workers": len(running_ids),
            "stalled_workers": len(stalled_ids),
            "orphaned_workers": len(orphaned_ids),
        }

    def shutdown(self) -> None:
        with self._lock:
            services = list(self._services.values())
            self._services.clear()
        for service in services:
            service.shutdown()

    def _new_service(self) -> MonitorService:
        arguments: dict[str, Any] = {
            "client_factory": self._client_factory,
            "repository": self.repository,
        }
        if self._notifier is not None:
            arguments["notifier"] = self._notifier
        return MonitorService(**arguments)

    def _require_monitor(self, monitoring_id: int, user_id: int) -> PersistedMonitor:
        monitor = self.repository.get_monitor(monitoring_id, user_id=user_id)
        if monitor is None:
            raise MonitoringNotFound(f"Monitoramento {monitoring_id} não encontrado.")
        return monitor

    @staticmethod
    def _snapshot_from_monitor(
        monitor: PersistedMonitor,
        *,
        message: str | None = None,
    ) -> MonitorSnapshot:
        return MonitorSnapshot(
            monitoring_id=monitor.id,
            running=False,
            status="PARADO",
            message=message or "Monitoramento pausado.",
            checked_at=monitor.last_checked_at,
            last_result=monitor.last_result,
            availability_changed_at=monitor.availability_changed_at,
            query={
                "origin": monitor.origin,
                "destination": monitor.destination,
                "travel_date": monitor.travel_date.isoformat(),
                "travel_class": monitor.travel_class,
                "passengers": monitor.passengers,
                "check_interval_seconds": monitor.interval_seconds,
                "whatsapp_enabled": monitor.whatsapp_enabled,
                "sms_enabled": monitor.sms_enabled,
            },
        )
