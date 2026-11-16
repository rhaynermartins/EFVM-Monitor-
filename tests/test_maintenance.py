from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import NoReturn

import httpx
import pytest
from fastapi.testclient import TestClient

from efvm_monitor.checker import AvailabilityStatus, EFVMClient, PortalError
from efvm_monitor.cli import main
from efvm_monitor.config import Settings, travel_today
from efvm_monitor.database import MonitoringRepository
from efvm_monitor.manager import MonitoringManager
from efvm_monitor.web import create_app


@pytest.fixture
def trip() -> Settings:
    return Settings.for_query(
        origin="7185",
        destination="7157",
        origin_label="Belo Horizonte",
        destination_label="Pedro Nolasco",
        travel_date=travel_today() + timedelta(days=7),
        travel_class="Executiva",
        check_interval_seconds=1800,
    )


def unexpected_call(*_args: object, **_kwargs: object) -> NoReturn:
    pytest.fail("A suspensão não deve iniciar clientes, consultar a Vale nem enviar alertas.")


def test_vale_maintenance_preserves_saved_monitors_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trip: Settings
) -> None:
    monkeypatch.setenv("EFVM_VALE_MONITORING_PAUSED", "true")
    storage = MonitoringRepository(tmp_path / "maintenance.db")
    storage.initialize()
    monitor = storage.create_monitor(trip)
    storage.record_check(
        monitor.id, AvailabilityStatus.SEM_VAGA, "Resultado anterior à pausa.",
        "2026-11-10T13:00:00-03:00",
    )
    saved = storage.get_monitor(monitor.id)
    history = storage.history(monitor.id)
    manager = MonitoringManager(storage, client_factory=unexpected_call, notifier=unexpected_call)
    app = create_app(
        manager=manager, catalog_provider=unexpected_call, authentication_enabled=False,
    )
    prefix = f"/api/monitoramentos/{monitor.id}"
    payload = {
        "origin": trip.origin,
        "destination": trip.destination,
        "travel_date": trip.travel_date.isoformat(),
        "travel_class": trip.travel_class,
        "interval_seconds": trip.check_interval_seconds,
    }

    with TestClient(app, headers={"X-CSRF-Token": "test-csrf-token"}) as client:
        page = client.get("/")
        assert 'data-monitoring-paused="true"' in page.text
        assert "Monitoramento temporariamente indisponível." in page.text
        assert 'href="https://tremdepassageiros.vale.com/"' in page.text
        assert client.get("/api/catalogo").status_code == 503
        assert client.post("/api/monitoramento", json=payload).status_code == 503
        assert client.post("/api/monitoramentos", json=payload).status_code == 503
        assert client.post(f"{prefix}/retomar").status_code == 503
        assert client.post("/api/monitoramentos/999/retomar").status_code == 404

        item, = client.get("/api/monitoramentos").json()["items"]
        assert item == client.get(prefix).json()
        assert not item["running"]
        assert item["status"] == "PARADO"
        assert item["query"]["origin_label"] == trip.origin_label
        assert item["query"]["destination_label"] == trip.destination_label
        assert client.get(f"{prefix}/historico").json()["items"] == [
            entry.to_dict() for entry in history
        ]

        health = client.get("/healthz?details=true")
        assert health.status_code == 200
        assert health.json()["integrations"] == {"vale": "suspended"}
        assert health.json()["manager"] == {
            "status": "ok",
            "active_monitors": 1,
            "registered_workers": 0,
            "running_workers": 0,
            "stalled_workers": 0,
            "orphaned_workers": 0,
        }

    assert saved is not None and saved.active
    assert storage.list_monitors() == [saved]
    assert storage.history(monitor.id) == history


def test_vale_maintenance_blocks_direct_queries_and_cli_watch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], trip: Settings
) -> None:
    monkeypatch.setenv("EFVM_VALE_MONITORING_PAUSED", "true")
    monkeypatch.setattr(httpx.Client, "post", unexpected_call)

    with EFVMClient(trip) as client:
        assert client.check().status == AvailabilityStatus.ERRO
        with pytest.raises(PortalError, match="temporariamente indisponível"):
            client.get_public_catalog()

    assert main(["--watch"]) == 2
    assert "ERRO | Monitoramento temporariamente indisponível." in capsys.readouterr().err
