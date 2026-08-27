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
from efvm_monitor.notifier import send_availability_alert

LOGGER = logging.getLogger(__name__)


class MonitorAlreadyRunning(RuntimeError):
    """Indica tentativa de iniciar um segundo monitor."""


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    running: bool = False
    status: str = "PARADO"
    message: str = "Nenhum monitoramento iniciado."
    checked_at: str | None = None
    query: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MonitorService:
    """Mantém o estado do monitor e impede execuções simultâneas."""

    def __init__(
        self,
        client_factory: Callable[[Settings], EFVMClient] = EFVMClient,
        notifier: Callable[[Settings, Any], None] = send_availability_alert,
    ) -> None:
        self._client_factory = client_factory
        self._notifier = notifier
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot = MonitorSnapshot()

    def start(self, settings: Settings) -> MonitorSnapshot:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise MonitorAlreadyRunning("Já existe um monitoramento em execução.")

            self._stop_event = threading.Event()
            self._snapshot = MonitorSnapshot(
                running=True,
                status="AGUARDANDO",
                message="Primeira consulta em andamento.",
                query=self._query_summary(settings),
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(settings, self._stop_event),
                name="efvm-availability-monitor",
                daemon=True,
            )
            self._thread.start()
            return self._snapshot

    def stop(self) -> MonitorSnapshot:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._snapshot = MonitorSnapshot(
                    status="PARADO",
                    message="O monitoramento já estava parado.",
                    checked_at=self._snapshot.checked_at,
                    query=self._snapshot.query,
                )
                return self._snapshot
            self._stop_event.set()
            self._snapshot = MonitorSnapshot(
                running=True,
                status="ENCERRANDO",
                message="Encerramento solicitado.",
                checked_at=self._snapshot.checked_at,
                query=self._snapshot.query,
            )
            return self._snapshot

    def snapshot(self) -> MonitorSnapshot:
        with self._lock:
            return self._snapshot

    def shutdown(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    def _run(self, settings: Settings, stop_event: threading.Event) -> None:
        previous_status: AvailabilityStatus | None = None
        try:
            with self._client_factory(settings) as client:
                while not stop_event.is_set():
                    result = client.check()
                    self._set_result(result.status, result.message)
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
            self._set_result(AvailabilityStatus.ERRO, str(exc))
        finally:
            with self._lock:
                self._snapshot = MonitorSnapshot(
                    running=False,
                    status="PARADO",
                    message="Monitoramento encerrado.",
                    checked_at=self._snapshot.checked_at,
                    query=self._snapshot.query,
                )

    def _set_result(self, status: AvailabilityStatus, message: str) -> None:
        with self._lock:
            self._snapshot = MonitorSnapshot(
                running=True,
                status=status.value,
                message=message,
                checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
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
