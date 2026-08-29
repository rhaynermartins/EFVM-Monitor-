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


class TransitionClient(FakeClient):
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.statuses = [
            AvailabilityStatus.SEM_VAGA,
            AvailabilityStatus.TEM_VAGA,
            AvailabilityStatus.TEM_VAGA,
            AvailabilityStatus.SEM_VAGA,
            AvailabilityStatus.TEM_VAGA,
        ]

    def check(self) -> AvailabilityResult:
        index = min(self.checks, len(self.statuses) - 1)
        status = self.statuses[index]
        self.checks += 1
        return AvailabilityResult(
            status,
            f"Resultado {status.value}.",
            int(status.value == "TEM_VAGA"),
        )


class RecoveringClient(FakeClient):
    attempts = 0

    def check(self) -> AvailabilityResult:
        type(self).attempts += 1
        if type(self).attempts == 1:
            raise RuntimeError("Falha inesperada temporária.")
        return AvailabilityResult(AvailabilityStatus.SEM_VAGA, "Consulta recuperada.")


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
        notifier=lambda _settings, result, *_: alerts.append(result),
    )

    started = service.start(settings())
    assert started.running is True
    assert started.status == "AGUARDANDO"

    wait_for_status(service, "TEM_VAGA")
    assert len(alerts) == 1
    assert service.snapshot().query["passengers"] == 1

    service.stop()
    wait_for_status(service, "PARADO")


def test_notification_failure_does_not_stop_monitor() -> None:
    def unavailable_notifier(*_: object) -> None:
        raise RuntimeError("Canal temporariamente indisponível.")

    service = MonitorService(
        client_factory=FakeClient,
        notifier=unavailable_notifier,
    )

    service.start(settings())
    wait_for_status(service, "TEM_VAGA")

    assert service.snapshot().running is True
    assert service.snapshot().status == "TEM_VAGA"

    service.stop()
    wait_for_status(service, "PARADO")


def test_alerts_once_per_new_availability_transition() -> None:
    alerts: list[AvailabilityResult] = []
    service = MonitorService(
        client_factory=TransitionClient,
        notifier=lambda _settings, result, *_: alerts.append(result),
    )

    service.start(settings())
    deadline = time.monotonic() + 1
    while len(alerts) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(alerts) == 2

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


def test_monitor_recreates_worker_client_after_unexpected_failure() -> None:
    RecoveringClient.attempts = 0
    service = MonitorService(
        client_factory=RecoveringClient,
        notifier=lambda *_: None,
    )

    service.start(settings())
    wait_for_status(service, "SEM_VAGA")

    assert RecoveringClient.attempts >= 2
    assert service.snapshot().running is True
    service.stop()
    wait_for_status(service, "PARADO")
