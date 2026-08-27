from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from efvm_monitor.config import Settings
from efvm_monitor.monitor import MonitorSnapshot
from efvm_monitor.web import create_app


CATALOG = {
    "stations": [
        {"id": 7185, "name": "Belo Horizonte", "state": "MG"},
        {"id": 7157, "name": "Pedro Nolasco (Cariacica / Vitória)", "state": "ES"},
    ],
    "classes": [
        {"id": 43, "name": "Econômica"},
        {"id": 44, "name": "Executiva"},
    ],
    "sale_window_days": 45,
}


class StubMonitor:
    def __init__(self) -> None:
        self.current = MonitorSnapshot()
        self.started_with: Settings | None = None
        self.was_shutdown = False

    def start(self, settings: Settings) -> MonitorSnapshot:
        self.started_with = settings
        self.current = MonitorSnapshot(
            running=True,
            status="AGUARDANDO",
            message="Primeira consulta em andamento.",
            query={"origin": settings.origin, "destination": settings.destination},
        )
        return self.current

    def stop(self) -> MonitorSnapshot:
        self.current = MonitorSnapshot(status="PARADO", message="Monitoramento encerrado.")
        return self.current

    def snapshot(self) -> MonitorSnapshot:
        return self.current

    def shutdown(self) -> None:
        self.was_shutdown = True


@pytest.fixture
def web_client() -> Iterator[tuple[TestClient, StubMonitor]]:
    monitor = StubMonitor()
    application = create_app(monitor=monitor, catalog_provider=lambda: CATALOG)
    with TestClient(application) as client:
        yield client, monitor


def valid_payload() -> dict[str, Any]:
    return {
        "origin": "7185",
        "destination": "7157",
        "travel_date": "2026-09-15",
        "travel_class": "Econômica",
        "passengers": 1,
        "interval_seconds": 300,
    }


def test_home_renders_complete_form(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, _ = web_client

    response = client.get("/")

    assert response.status_code == 200
    assert "Configure a viagem" in response.text
    assert 'id="origin"' in response.text
    assert 'id="destination"' in response.text
    assert 'id="travel-date"' in response.text
    assert 'id="travel-class"' in response.text
    assert 'id="passengers"' in response.text


def test_catalog_returns_stations_and_classes(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, _ = web_client

    response = client.get("/api/catalogo")

    assert response.status_code == 200
    assert response.json() == CATALOG


def test_start_status_and_stop_monitoring(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, monitor = web_client

    start_response = client.post("/api/monitoramento", json=valid_payload())
    status_response = client.get("/api/monitoramento")
    stop_response = client.delete("/api/monitoramento")

    assert start_response.status_code == 202
    assert start_response.json()["status"] == "AGUARDANDO"
    assert status_response.json()["running"] is True
    assert stop_response.json()["status"] == "PARADO"
    assert monitor.started_with is not None
    assert monitor.started_with.travel_class == "Econômica"
    assert monitor.started_with.passengers == 1


def test_rejects_same_origin_and_destination(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, _ = web_client
    payload = valid_payload()
    payload["destination"] = payload["origin"]

    response = client.post("/api/monitoramento", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "Origem e destino devem ser diferentes."


def test_rejects_more_than_one_passenger(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, _ = web_client
    payload = valid_payload()
    payload["passengers"] = 2

    response = client.post("/api/monitoramento", json=payload)

    assert response.status_code == 422
