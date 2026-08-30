from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from efvm_monitor import config, monitor
from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import ConfigurationError, Settings
from efvm_monitor.database import MonitoringRepository, MonitorStatus, PersistedMonitor
from efvm_monitor.manager import MonitoringManager
from efvm_monitor.monitor import MonitorService
from efvm_monitor.web import create_app

TRAVEL_DAY = date(2026, 9, 7)
INTERVALS = [300, 600, 900, 1800, 3600, 10800]
INVALID_INTERVALS = [-1, 0, 1, 60, 120, 240, 299, 301, 1200, 2700, 7200, 86400]


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, datetime]:
    current = {"now": datetime(2026, 9, 7, 15, tzinfo=UTC)}

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = current["now"]
            return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(config, "datetime", FixedDateTime)
    monkeypatch.setattr(monitor, "datetime", FixedDateTime)
    return current


def query(day: date = TRAVEL_DAY, interval: int = 300, origin: str = "1") -> Settings:
    return Settings.for_query(
        origin=origin, destination="2", travel_date=day, travel_class="Econômica",
        check_interval_seconds=interval, allow_today=True,
    )


def saved_settings(saved: PersistedMonitor) -> Settings:
    return saved.to_settings(
        timeout_seconds=30, log_level="INFO", base_url="https://example.test",
        railway_code="03", alert_webhook_url=None,
    )


@pytest.fixture
def runtime(tmp_path: Path, clock: dict[str, datetime]):
    storage = MonitoringRepository(tmp_path / "policy.db")
    storage.initialize()
    checks: list[str] = []

    class Client:
        def __init__(self, settings: Settings):
            self.settings = settings

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def check(self):
            checks.append(self.settings.origin)
            return AvailabilityResult(AvailabilityStatus.SEM_VAGA, "Sem vagas.")

    manager = MonitoringManager(storage, client_factory=Client, notifier=lambda *_: None)
    yield manager, storage, checks
    manager.shutdown()


@pytest.fixture
def api(runtime) -> Iterator[TestClient]:
    manager, storage, _ = runtime
    app = create_app(
        manager=manager, repository=storage, authentication_enabled=False,
        catalog_provider=lambda: {"stations": [], "classes": [], "sale_window_days": 45},
    )
    with TestClient(app) as client:
        client.headers["X-CSRF-Token"] = "test-csrf-token"
        yield client


def payload(interval: int) -> dict:
    return {
        "origin": "1", "destination": "2", "travel_date": "2026-09-08",
        "travel_class": "Econômica", "passengers": 1, "interval_seconds": interval,
    }


def wait_until(predicate) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("O worker não atingiu o estado esperado.")


@pytest.mark.parametrize("path", ["/api/monitoramento", "/api/monitoramentos"])
@pytest.mark.parametrize("interval", INTERVALS)
def test_accepts_only_supported_intervals(api: TestClient, interval: int, path: str) -> None:
    response = api.post(path, json=payload(interval))
    assert response.status_code == 202
    assert response.json()["query"]["check_interval_seconds"] == interval


@pytest.mark.parametrize("path", ["/api/monitoramento", "/api/monitoramentos"])
@pytest.mark.parametrize("interval", INVALID_INTERVALS)
def test_rejects_manipulated_intervals(api: TestClient, interval: int, path: str) -> None:
    assert api.post(path, json=payload(interval)).status_code == 422
    with pytest.raises(ConfigurationError, match="intervalo"):
        query(interval=interval)
    assert api.get("/api/monitoramentos").json()["items"] == []


@pytest.mark.parametrize("interval", [3600, 10800])
def test_hour_intervals_survive_restart(runtime, interval: int) -> None:
    manager, storage, _ = runtime
    created = manager.create(query(TRAVEL_DAY + timedelta(days=1), interval))
    manager.shutdown()
    restored = manager.restore_all(saved_settings)
    assert restored[0].monitoring_id == created.monitoring_id
    assert restored[0].query["check_interval_seconds"] == interval
    assert storage.get_monitor(created.monitoring_id).interval_seconds == interval


def test_form_contains_exact_interval_choices(api: TestClient) -> None:
    html = api.get("/").text
    select = re.search(r'<select id="interval"[^>]*>(.*?)</select>', html, re.S).group(1)
    assert re.findall(r'<option value="(\d+)"[^>]*>([^<]+)</option>', select) == [
        ("300", "5 minutos"), ("600", "10 minutos"), ("900", "15 minutos"),
        ("1800", "30 minutos"), ("3600", "1 hora"), ("10800", "3 horas"),
    ]


@pytest.mark.parametrize("days_ahead", [0, 1])
def test_today_and_future_resume_even_when_utc_date_has_changed(
    api: TestClient, runtime, clock: dict[str, datetime], days_ahead: int,
) -> None:
    manager, storage, checks = runtime
    clock["now"] = datetime(2026, 9, 8, 2, 59, 59, tzinfo=UTC)
    assert config.travel_today() == TRAVEL_DAY
    saved = storage.create_monitor(query(TRAVEL_DAY + timedelta(days=days_ahead)))
    manager.restore_all(saved_settings)
    wait_until(lambda: bool(checks))
    assert manager.snapshot(saved.id).running
    manager.pause(saved.id)
    assert api.post(f"/api/monitoramentos/{saved.id}/retomar").status_code == 200
    assert storage.get_monitor(saved.id).active


def test_restart_pauses_expired_monitor_and_preserves_history(api: TestClient, runtime, clock):
    manager, storage, checks = runtime
    expired = storage.create_monitor(query())
    future = storage.create_monitor(query(TRAVEL_DAY + timedelta(days=2), origin="3"))
    storage.record_check(
        expired.id, AvailabilityStatus.SEM_VAGA, "Preservar", "2026-09-07T10:00:00-03:00",
    )
    history_before = storage.history(expired.id)
    clock["now"] = datetime(2026, 9, 8, 3, tzinfo=UTC)
    manager.restore_all(saved_settings)
    wait_until(lambda: "3" in checks)
    assert "1" not in checks
    assert storage.get_monitor(expired.id).status is MonitorStatus.PAUSED
    assert storage.history(expired.id) == history_before
    assert manager.snapshot(future.id).running
    assert not manager.snapshot(expired.id).running
    response = api.post(f"/api/monitoramentos/{expired.id}/retomar")
    assert response.status_code == 400
    assert "Essa viagem já expirou" in response.json()["detail"]
    # A caller cannot bypass the persisted date by supplying future settings.
    with pytest.raises(ConfigurationError, match="expirou"):
        manager.resume(expired.id, query(TRAVEL_DAY + timedelta(days=2)))
    assert len(api.get(f"/api/monitoramentos/{expired.id}/historico").json()["items"]) == 1
    assert api.delete(f"/api/monitoramentos/{expired.id}").status_code == 200
    assert storage.get_monitor(expired.id) is None  # Existing soft delete, only on request.
    with storage._connect() as connection:
        row = connection.execute(
            "SELECT removed_at FROM monitoring_jobs WHERE id = ?", (expired.id,),
        ).fetchone()
    assert row is not None and row["removed_at"] is not None
    assert storage.history(expired.id) == history_before
    assert manager.snapshot(future.id).running


@pytest.mark.parametrize("fails", [False, True])
def test_worker_expires_at_midnight_during_normal_or_retry_wait(
    runtime, clock, monkeypatch: pytest.MonkeyPatch, fails: bool,
):
    manager, storage, checks = runtime
    clock["now"] = datetime(2026, 9, 8, 2, 59, 59, tzinfo=UTC)
    original_wait = MonitorService._wait_for_next_check
    waits = []

    class BoundaryWait:
        def wait(self, timeout):
            waits.append(timeout)
            clock["now"] += timedelta(seconds=timeout)
            return False

    def wait(settings, stop_event):
        if settings.origin == "1":
            return original_wait(settings, BoundaryWait())
        return original_wait(settings, stop_event)

    monkeypatch.setattr(MonitorService, "_wait_for_next_check", staticmethod(wait))
    if fails:
        original_client = manager._client_factory

        class FailingClient(original_client):
            def check(self):
                checks.append(self.settings.origin)
                if self.settings.origin == "1":
                    raise RuntimeError("Falha controlada")
                return AvailabilityResult(AvailabilityStatus.SEM_VAGA, "Sem vagas")

        manager._client_factory = FailingClient
    other = manager.create(query(TRAVEL_DAY + timedelta(days=2), origin="3"))
    started = manager.create(query(interval=10800))
    worker = manager._services[started.monitoring_id]
    wait_until(lambda: not worker._thread.is_alive())
    assert waits == [1.0]  # Never sleep three hours past the journey's local midnight.
    assert checks.count("1") == 1
    saved = storage.get_monitor(started.monitoring_id)
    assert saved.status is MonitorStatus.PAUSED
    assert saved.removed_at is None
    assert len(storage.history(saved.id)) == 1
    assert storage.history(saved.id)[0].result == ("ERRO" if fails else "SEM_VAGA")
    assert not worker.snapshot().running
    assert manager.snapshot(other.monitoring_id).running
    health = manager.operational_status()
    assert health["status"] == "ok"
    assert health["registered_workers"] == health["running_workers"] == 1
    assert health["stalled_workers"] == health["orphaned_workers"] == 0
    manager.shutdown()
    manager.restore_all(saved_settings)
    assert checks.count("1") == 1
    assert not manager.snapshot(saved.id).running


def test_single_monitor_restore_and_direct_start_reject_expired(runtime, clock):
    _, storage, _ = runtime
    settings = query()
    saved = storage.create_monitor(settings)
    clock["now"] = datetime(2026, 9, 8, 3, tzinfo=UTC)
    service = MonitorService(repository=storage, client_factory=lambda _: pytest.fail("No client"))
    restored = service.restore(saved, settings)
    assert not restored.running
    assert storage.get_monitor(saved.id).status is MonitorStatus.PAUSED
    with pytest.raises(ConfigurationError, match="expirou"):
        service.start(settings, monitoring_id=saved.id)
    assert service._thread is None


def test_wait_respects_interval_before_expiration(clock):
    class ImmediateEvent(threading.Event):
        def wait(self, timeout=None):
            assert timeout == 10800
            return True

    assert MonitorService._wait_for_next_check(query(interval=10800), ImmediateEvent())
