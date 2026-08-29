from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository
from efvm_monitor.monitor import MonitorService, MonitorSnapshot
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

    def start(self, settings: Settings, user_id: int = 1) -> MonitorSnapshot:
        assert user_id == 1
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

    def history(self, _limit: int = 100) -> list[dict[str, Any]]:
        return []

    def shutdown(self) -> None:
        self.was_shutdown = True


class PersistentFakeClient:
    def __init__(self, _: Settings) -> None:
        pass

    def __enter__(self) -> PersistentFakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def check(self) -> AvailabilityResult:
        return AvailabilityResult(AvailabilityStatus.SEM_VAGA, "Ainda não há passagens.")


@pytest.fixture
def web_client() -> Iterator[tuple[TestClient, StubMonitor]]:
    monitor = StubMonitor()
    application = create_app(
        monitor=monitor,
        catalog_provider=lambda: CATALOG,
        authentication_enabled=False,
    )
    with TestClient(application) as client:
        client.headers["X-CSRF-Token"] = "test-csrf-token"
        yield client, monitor


def valid_payload() -> dict[str, Any]:
    return {
        "origin": "7185",
        "destination": "7157",
        "travel_date": (date.today() + timedelta(days=10)).isoformat(),
        "travel_class": "Econômica",
        "passengers": 1,
        "interval_seconds": 300,
        "whatsapp_enabled": False,
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
    assert 'id="whatsapp-alerts"' in response.text
    assert response.text.index("Configure a viagem") < response.text.index("Acompanhe o estado")


def test_home_renders_phase_eight_guidance_and_interface_states(
    web_client: tuple[TestClient, StubMonitor],
) -> None:
    client, _ = web_client

    response = client.get("/")

    assert response.status_code == 200
    assert "O EFVM Monitor procura a passagem por você e avisa quando aparecer." in response.text
    assert 'id="onboarding"' in response.text
    assert 'id="onboarding-monitor"' in response.text
    assert 'id="onboarding-install"' in response.text
    assert 'id="onboarding-push"' in response.text
    assert 'id="device-summary"' in response.text
    assert 'id="connection-banner"' in response.text
    assert 'id="dashboard-state"' in response.text
    assert 'id="next-check"' in response.text
    assert 'id="ios-install-help"' in response.text
    assert 'id="android-install-help"' in response.text
    assert 'class="brand-icon"' in response.text
    assert "Abra esta página pelo Safari." in response.text
    assert "Adicionar à Tela de Início" in response.text
    assert "Abra esta página no Chrome." in response.text
    assert "Instalar app" in response.text
    assert response.text.index('id="onboarding"') < response.text.index('id="monitor-form"')


def test_phase_eight_assets_include_accessible_mobile_states(
    web_client: tuple[TestClient, StubMonitor],
) -> None:
    client, _ = web_client

    javascript = client.get("/static/app.js")
    stylesheet = client.get("/static/style.css")

    assert javascript.status_code == 200
    assert stylesheet.status_code == 200
    assert "function configureInstallationExperience()" in javascript.text
    assert "function updateConnectionState()" in javascript.text
    assert 'elements.installTitle.textContent = "Aplicativo instalado"' in javascript.text
    assert "Passagem encontrada 🔔" in javascript.text
    assert "Próxima verificação prevista" in javascript.text
    assert ':where(a, button, input, select):focus-visible' in stylesheet.text
    assert ".monitor-card-heading" in stylesheet.text
    assert "flex-wrap: wrap" in stylesheet.text
    assert ".monitor-message" in stylesheet.text
    assert "overflow-wrap: anywhere" in stylesheet.text
    assert '@media (max-width: 600px)' in stylesheet.text


def test_catalog_returns_stations_and_classes(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, _ = web_client

    response = client.get("/api/catalogo")

    assert response.status_code == 200
    assert response.json() == CATALOG


def test_empty_history_is_available(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, _ = web_client

    response = client.get("/api/monitoramento/historico")

    assert response.status_code == 200
    assert response.json() == {"monitoring_id": None, "items": []}


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
    assert monitor.started_with.whatsapp_enabled is False
    assert monitor.started_with.origin_label == "Belo Horizonte"


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


def test_rejects_past_travel_date(web_client: tuple[TestClient, StubMonitor]) -> None:
    client, _ = web_client
    payload = valid_payload()
    payload["travel_date"] = (date.today() - timedelta(days=1)).isoformat()

    response = client.post("/api/monitoramento", json=payload)

    assert response.status_code == 400
    assert response.json()["detail"] == "A data deve ser posterior ao dia atual."


def wait_for_api_status(client: TestClient, expected: str) -> dict[str, Any]:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        state = client.get("/api/monitoramento").json()
        if state["status"] == expected:
            return state
        time.sleep(0.01)
    raise AssertionError(f"A API não chegou ao estado {expected}.")


def persistent_app(database_path: Path) -> tuple[Any, MonitoringRepository]:
    storage = MonitoringRepository(database_path)
    service = MonitorService(
        client_factory=PersistentFakeClient,
        notifier=lambda *_: None,
        repository=storage,
    )
    application = create_app(
        monitor=service,
        repository=storage,
        catalog_provider=lambda: CATALOG,
        authentication_enabled=False,
    )
    return application, storage


def test_healthcheck_is_public_and_confirms_database(tmp_path: Path) -> None:
    storage = MonitoringRepository(tmp_path / "health.db")
    application = create_app(
        repository=storage,
        catalog_provider=lambda: CATALOG,
    )

    with TestClient(application) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_recovers_active_monitor_and_history_after_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "persisted.db"
    first_app, first_storage = persistent_app(database_path)

    with TestClient(first_app) as first_client:
        first_client.headers["X-CSRF-Token"] = "test-csrf-token"
        response = first_client.post("/api/monitoramento", json=valid_payload())
        assert response.status_code == 202
        first_state = wait_for_api_status(first_client, "SEM_VAGA")
        monitoring_id = first_state["monitoring_id"]
        assert len(first_client.get("/api/monitoramento/historico").json()["items"]) == 1

    persisted = first_storage.get_monitor(monitoring_id)
    assert persisted is not None
    assert persisted.active is True

    restarted_app, _ = persistent_app(database_path)
    with TestClient(restarted_app) as restarted_client:
        restarted_state = wait_for_api_status(restarted_client, "SEM_VAGA")
        restarted_history = restarted_client.get("/api/monitoramento/historico").json()

        assert restarted_state["monitoring_id"] == monitoring_id
        assert restarted_state["query"]["origin"] == "7185"
        assert len(restarted_history["items"]) == 2
