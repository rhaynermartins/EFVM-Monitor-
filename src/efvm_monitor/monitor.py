"""Controle de um único monitoramento em segundo plano."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import httpx

from efvm_monitor.checker import AvailabilityStatus, EFVMClient
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, MonitorStatus, PersistedMonitor
from efvm_monitor.notifier import send_availability_alert

LOGGER = logging.getLogger(__name__)


class MonitorAlreadyRunning(RuntimeError):
    """Indica tentativa de iniciar um segundo monitor."""


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    monitoring_id: int | None = None
    running: bool = False
    status: str = "PARADO"
    message: str = "Nenhum monitoramento iniciado."
    checked_at: str | None = None
    last_result: str | None = None
    availability_changed_at: str | None = None
    query: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MonitorService:
    """Mantém o estado do monitor e impede execuções simultâneas."""

    def __init__(
        self,
        client_factory: Callable[[Settings], EFVMClient] = EFVMClient,
        notifier: Callable[[Settings, Any], None] = send_availability_alert,
        repository: MonitoringRepository | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._notifier = notifier
        self._repository = repository
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = MonitorSnapshot()

    def start(
        self,
        settings: Settings,
        monitoring_id: int | None = None,
    ) -> MonitorSnapshot:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise MonitorAlreadyRunning("Já existe um monitoramento em execução.")

            persisted: PersistedMonitor | None = None
            if self._repository is not None:
                if monitoring_id is None:
                    persisted = self._repository.create_monitor(settings)
                    monitoring_id = persisted.id
                else:
                    self._repository.set_status(monitoring_id, MonitorStatus.ACTIVE)
                    persisted = self._repository.get_monitor(monitoring_id)

            previous_status = self._availability_status(
                persisted.last_result if persisted is not None else None
            )
            self._stop_event = threading.Event()
            self._snapshot = MonitorSnapshot(
                monitoring_id=monitoring_id,
                running=True,
                status="AGUARDANDO",
                message="Primeira consulta em andamento.",
                checked_at=persisted.last_checked_at if persisted is not None else None,
                last_result=persisted.last_result if persisted is not None else None,
                availability_changed_at=(
                    persisted.availability_changed_at if persisted is not None else None
                ),
                query=self._query_summary(settings),
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(settings, self._stop_event, monitoring_id, previous_status),
                name="efvm-availability-monitor",
                daemon=True,
            )
            self._thread.start()
            return self._snapshot

    def stop(self) -> MonitorSnapshot:
        with self._lock:
            thread = self._thread
            monitoring_id = self._snapshot.monitoring_id
            if self._repository is not None and monitoring_id is not None:
                self._repository.set_status(monitoring_id, MonitorStatus.PAUSED)
            if thread is None or not thread.is_alive():
                self._snapshot = MonitorSnapshot(
                    monitoring_id=monitoring_id,
                    status="PARADO",
                    message="O monitoramento já estava parado.",
                    checked_at=self._snapshot.checked_at,
                    last_result=self._snapshot.last_result,
                    availability_changed_at=self._snapshot.availability_changed_at,
                    query=self._snapshot.query,
                )
                return self._snapshot
            self._stop_event.set()
            self._snapshot = MonitorSnapshot(
                monitoring_id=monitoring_id,
                running=True,
                status="ENCERRANDO",
                message="Encerramento solicitado.",
                checked_at=self._snapshot.checked_at,
                last_result=self._snapshot.last_result,
                availability_changed_at=self._snapshot.availability_changed_at,
                query=self._snapshot.query,
            )
            return self._snapshot

    def snapshot(self) -> MonitorSnapshot:
        with self._lock:
            return self._snapshot

    def restore(
        self,
        monitor: PersistedMonitor,
        settings: Settings | None = None,
    ) -> MonitorSnapshot:
        if monitor.active:
            if settings is None:
                raise ValueError("A configuração é obrigatória para retomar um monitor ativo.")
            return self.start(settings, monitoring_id=monitor.id)

        with self._lock:
            self._snapshot = MonitorSnapshot(
                monitoring_id=monitor.id,
                status="PARADO",
                message="Monitoramento recuperado do banco local.",
                checked_at=monitor.last_checked_at,
                last_result=monitor.last_result,
                availability_changed_at=monitor.availability_changed_at,
                query=self._query_summary_from_monitor(monitor),
            )
            return self._snapshot

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            monitoring_id = self._snapshot.monitoring_id
        if self._repository is None or monitoring_id is None:
            return []
        return [entry.to_dict() for entry in self._repository.history(monitoring_id, limit)]

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def _run(
        self,
        settings: Settings,
        stop_event: threading.Event,
        monitoring_id: int | None,
        previous_status: AvailabilityStatus | None,
    ) -> None:
        try:
            with self._client_factory(settings) as client:
                while not stop_event.is_set():
                    result = client.check()
                    self._set_result(result.status, result.message, monitoring_id)
                    became_available = (
                        result.status is AvailabilityStatus.TEM_VAGA
                        and previous_status is not result.status
                    )
                    if became_available:
                        try:
                            self._notifier(settings, result)
                        except httpx.HTTPError as exc:
                            LOGGER.error("O alerta por webhook não foi enviado: %s", exc)
                    previous_status = result.status
                    if stop_event.wait(settings.check_interval_seconds):
                        break
        except Exception as exc:
            LOGGER.exception("O monitoramento foi interrompido: %s", exc)
            try:
                self._set_result(AvailabilityStatus.ERRO, str(exc), monitoring_id)
            except Exception:
                LOGGER.exception("O estado de erro não pôde ser persistido.")
                self._set_runtime_error(str(exc), monitoring_id)
        finally:
            with self._lock:
                self._snapshot = MonitorSnapshot(
                    monitoring_id=monitoring_id,
                    running=False,
                    status="PARADO",
                    message="Monitoramento encerrado.",
                    checked_at=self._snapshot.checked_at,
                    last_result=self._snapshot.last_result,
                    availability_changed_at=self._snapshot.availability_changed_at,
                    query=self._snapshot.query,
                )

    def _set_result(
        self,
        status: AvailabilityStatus,
        message: str,
        monitoring_id: int | None,
    ) -> None:
        checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
        persisted: PersistedMonitor | None = None
        if self._repository is not None and monitoring_id is not None:
            self._repository.record_check(monitoring_id, status, message, checked_at)
            persisted = self._repository.get_monitor(monitoring_id)

        with self._lock:
            self._snapshot = MonitorSnapshot(
                monitoring_id=monitoring_id,
                running=True,
                status=status.value,
                message=message,
                checked_at=checked_at,
                last_result=status.value,
                availability_changed_at=(
                    persisted.availability_changed_at
                    if persisted is not None
                    else self._snapshot.availability_changed_at
                ),
                query=self._snapshot.query,
            )

    def _set_runtime_error(self, message: str, monitoring_id: int | None) -> None:
        with self._lock:
            self._snapshot = MonitorSnapshot(
                monitoring_id=monitoring_id,
                running=True,
                status=AvailabilityStatus.ERRO.value,
                message=message,
                checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                last_result=AvailabilityStatus.ERRO.value,
                availability_changed_at=self._snapshot.availability_changed_at,
                query=self._snapshot.query,
            )

    @staticmethod
    def _query_summary(settings: Settings) -> dict[str, Any]:
        return {
            "origin": settings.origin,
            "destination": settings.destination,
            "travel_date": settings.travel_date.isoformat(),
            "travel_class": settings.travel_class,
            "passengers": settings.passengers,
            "check_interval_seconds": settings.check_interval_seconds,
        }

    @staticmethod
    def _query_summary_from_monitor(monitor: PersistedMonitor) -> dict[str, Any]:
        return {
            "origin": monitor.origin,
            "destination": monitor.destination,
            "travel_date": monitor.travel_date.isoformat(),
            "travel_class": monitor.travel_class,
            "passengers": monitor.passengers,
            "check_interval_seconds": monitor.interval_seconds,
        }

    @staticmethod
    def _availability_status(value: str | None) -> AvailabilityStatus | None:
        if value not in {
            AvailabilityStatus.TEM_VAGA.value,
            AvailabilityStatus.SEM_VAGA.value,
        }:
            return None
        return AvailabilityStatus(value)
