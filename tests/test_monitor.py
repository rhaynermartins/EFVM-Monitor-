from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.monitor import MonitorAlreadyRunning, MonitorService


class FakeClient:
    def __init__(self, _: Settings) -> None:
        self.checks = 0

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def check(self) -> AvailabilityResult:
        self.checks += 1
        return AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Uma opção disponível.", 1)


def settings() -> Settings:
    return Settings(
        origin="7185",
        destination="7157",
        travel_date=date.today() + timedelta(days=1),
        travel_class="Econômica",
        passengers=1,
        check_interval_seconds=0.01,
        timeout_seconds=30,
        log_level="INFO",
        base_url="https://example.test",
        railway_code="03",
        alert_webhook_url=None,
    )


def wait_for_status(service: MonitorService, expected: str) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        if service.snapshot().status == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"O monitor não chegou ao estado {expected}.")


def test_monitor_reports_result_and_stops() -> None:
    alerts: list[AvailabilityResult] = []
    service = MonitorService(
        client_factory=FakeClient,
        notifier=lambda _settings, result: alerts.append(result),
    )

    started = service.start(settings())
    assert started.running is True
    assert started.status == "AGUARDANDO"

    wait_for_status(service, "TEM_VAGA")
    assert len(alerts) == 1
    assert service.snapshot().query["passengers"] == 1

    service.stop()
    wait_for_status(service, "PARADO")
    assert service.snapshot().running is False


def test_monitor_rejects_simultaneous_start() -> None:
    service = MonitorService(client_factory=FakeClient, notifier=lambda *_: None)
    service.start(settings())

    with pytest.raises(MonitorAlreadyRunning, match="Já existe"):
        service.start(settings())

    service.stop()
    wait_for_status(service, "PARADO")
