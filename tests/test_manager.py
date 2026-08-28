from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path

import pytest

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, PersistedMonitor
from efvm_monitor.manager import MonitoringManager, MonitoringNotFound


class RouteClient:
    def __init__(self, settings: Settings) -> None:
        self.status = (
            AvailabilityStatus.TEM_VAGA
            if settings.origin == "origem-a"
            else AvailabilityStatus.SEM_VAGA
        )

    def __enter__(self) -> RouteClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def check(self) -> AvailabilityResult:
        return AvailabilityResult(self.status, f"Resultado isolado: {self.status.value}.")


def settings(origin: str, destination: str) -> Settings:
    return Settings(
        origin=origin,
        destination=destination,
        travel_date=date.today() + timedelta(days=10),
        travel_class="Econômica",
        passengers=1,
        check_interval_seconds=60,
        timeout_seconds=30,
        log_level="INFO",
        base_url="https://example.test",
        railway_code="03",
        alert_webhook_url=None,
    )


def manager(tmp_path: Path) -> tuple[MonitoringManager, MonitoringRepository]:
    storage = MonitoringRepository(tmp_path / "multiple.db")
    storage.initialize()
    return (
        MonitoringManager(storage, client_factory=RouteClient, notifier=lambda *_: None),
        storage,
    )


def wait_for_status(service: MonitoringManager, monitoring_id: int, expected: str) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if service.snapshot(monitoring_id).status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"O monitor {monitoring_id} não chegou ao estado {expected}.")


def persisted_settings(monitor: PersistedMonitor) -> Settings:
    return settings(monitor.origin, monitor.destination)


def test_runs_independent_monitors_and_pauses_only_selected_one(tmp_path: Path) -> None:
    service, _ = manager(tmp_path)
    first = service.create(settings("origem-a", "destino-a"))
    second = service.create(settings("origem-b", "destino-b"))
    assert first.monitoring_id is not None
    assert second.monitoring_id is not None

    wait_for_status(service, first.monitoring_id, "TEM_VAGA")
    wait_for_status(service, second.monitoring_id, "SEM_VAGA")

    paused = service.pause(first.monitoring_id)

    assert paused.status == "PARADO"
    assert service.snapshot(second.monitoring_id).running is True
    assert len(service.list()) == 2
    service.shutdown()


def test_resumes_paused_monitor_with_same_id(tmp_path: Path) -> None:
    service, _ = manager(tmp_path)
    created = service.create(settings("origem-a", "destino-a"))
    assert created.monitoring_id is not None
    service.pause(created.monitoring_id)

    resumed = service.resume(
        created.monitoring_id,
        settings("origem-a", "destino-a"),
    )

    assert resumed.monitoring_id == created.monitoring_id
    assert resumed.running is True
    wait_for_status(service, created.monitoring_id, "TEM_VAGA")
    service.shutdown()


def test_soft_removal_preserves_database_without_listing_monitor(tmp_path: Path) -> None:
    service, storage = manager(tmp_path)
    created = service.create(settings("origem-a", "destino-a"))
    assert created.monitoring_id is not None
    wait_for_status(service, created.monitoring_id, "TEM_VAGA")

    service.remove(created.monitoring_id)

    assert service.list() == []
    assert storage.get_monitor(created.monitoring_id) is None
    assert storage.history(created.monitoring_id)
    with pytest.raises(MonitoringNotFound):
        service.snapshot(created.monitoring_id)


def test_restores_all_active_monitors_after_restart(tmp_path: Path) -> None:
    first_manager, storage = manager(tmp_path)
    first = first_manager.create(settings("origem-a", "destino-a"))
    second = first_manager.create(settings("origem-b", "destino-b"))
    assert first.monitoring_id is not None
    assert second.monitoring_id is not None
    wait_for_status(first_manager, first.monitoring_id, "TEM_VAGA")
    wait_for_status(first_manager, second.monitoring_id, "SEM_VAGA")
    first_manager.shutdown()

    restarted = MonitoringManager(
        storage,
        client_factory=RouteClient,
        notifier=lambda *_: None,
    )
    restored = restarted.restore_all(persisted_settings)

    assert {item.monitoring_id for item in restored} == {
        first.monitoring_id,
        second.monitoring_id,
    }
    wait_for_status(restarted, first.monitoring_id, "TEM_VAGA")
    wait_for_status(restarted, second.monitoring_id, "SEM_VAGA")
    restarted.shutdown()
