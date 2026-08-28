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


class MonitoringManager:
    """Mantém um executor isolado por ID e compartilha apenas a persistência."""

    def __init__(
        self,
        repository: MonitoringRepository,
        *,
        client_factory: Callable[[Settings], EFVMClient] = EFVMClient,
        notifier: Callable[[Settings, Any, int | None, str], None] | None = None,
    ) -> None:
        self.repository = repository
        self._client_factory = client_factory
        self._notifier = notifier
        self._services: dict[int, MonitorService] = {}
        self._lock = threading.RLock()

    def create(
        self,
        settings: Settings,
        user_id: int = LEGACY_USER_ID,
    ) -> MonitorSnapshot:
        service = self._new_service()
        snapshot = service.start(settings, user_id=user_id)
        if snapshot.monitoring_id is None:
            service.shutdown()
            raise RuntimeError("O monitoramento não recebeu um ID persistente.")
        with self._lock:
            self._services[snapshot.monitoring_id] = service
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
                LOGGER.error("Monitor %s não pôde ser retomado: %s", monitor.id, exc)
                self.repository.set_status(monitor.id, MonitorStatus.PAUSED)
                recovered = self.repository.get_monitor(
                    monitor.id,
                    user_id=monitor.user_id,
                )
                if recovered is not None:
                    restored.append(self._snapshot_from_monitor(recovered, message=str(exc)))
        return restored

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
