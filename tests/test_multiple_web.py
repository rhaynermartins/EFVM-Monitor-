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
from efvm_monitor.manager import MonitoringManager
from efvm_monitor.web import create_app

CATALOG = {
    "stations": [
        {"id": 1, "name": "Belo Horizonte", "state": "MG"},
        {"id": 2, "name": "Dois Irmãos", "state": "MG"},
        {"id": 3, "name": "Pedro Nolasco", "state": "ES"},
    ],
    "classes": [
        {"id": 43, "name": "Econômica"},
        {"id": 44, "name": "Executiva"},
    ],
    "sale_window_days": 45,
}


class ImmediateClient:
    def __init__(self, settings: Settings) -> None:
        self.origin = settings.origin

    def __enter__(self) -> ImmediateClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def check(self) -> AvailabilityResult:
        return AvailabilityResult(
            AvailabilityStatus.SEM_VAGA,
            f"Consulta independente da origem {self.origin}.",
        )


@pytest.fixture
def multiple_client(tmp_path: Path) -> Iterator[TestClient]:
    storage = MonitoringRepository(tmp_path / "multiple-web.db")
    storage.initialize()
    manager = MonitoringManager(
        storage,
        client_factory=ImmediateClient,
        notifier=lambda *_: None,
    )
    application = create_app(
        manager=manager,
        repository=storage,
        catalog_provider=lambda: CATALOG,
    )
    with TestClient(application) as client:
        yield client


def payload(origin: str, destination: str, travel_class: str = "Econômica") -> dict[str, Any]:
    return {
        "origin": origin,
        "destination": destination,
        "travel_date": (date.today() + timedelta(days=10)).isoformat(),
        "travel_class": travel_class,
        "passengers": 1,
        "interval_seconds": 60,
        "whatsapp_enabled": False,
        "sms_enabled": False,
    }


def wait_for_status(client: TestClient, monitoring_id: int, expected: str) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        response = client.get(f"/api/monitoramentos/{monitoring_id}")
        if response.json()["status"] == expected:
            return
        time.sleep(0.01)
    raise AssertionError(f"O monitor {monitoring_id} não chegou ao estado {expected}.")


def test_creates_lists_and_queries_multiple_monitorings(multiple_client: TestClient) -> None:
    first = multiple_client.post("/api/monitoramentos", json=payload("1", "2"))
    second = multiple_client.post(
        "/api/monitoramentos",
        json=payload("2", "3", "Executiva"),
    )
    first_id = first.json()["monitoring_id"]
    second_id = second.json()["monitoring_id"]
    wait_for_status(multiple_client, first_id, "SEM_VAGA")
    wait_for_status(multiple_client, second_id, "SEM_VAGA")

    listed = multiple_client.get("/api/monitoramentos").json()["items"]

    assert first.status_code == 202
    assert second.status_code == 202
    assert {item["monitoring_id"] for item in listed} == {first_id, second_id}
    assert multiple_client.get(f"/api/monitoramentos/{first_id}").status_code == 200


def test_pauses_resumes_and_removes_only_selected_monitor(multiple_client: TestClient) -> None:
    first_id = multiple_client.post(
        "/api/monitoramentos", json=payload("1", "2")
    ).json()["monitoring_id"]
    second_id = multiple_client.post(
        "/api/monitoramentos", json=payload("2", "3")
    ).json()["monitoring_id"]
    wait_for_status(multiple_client, first_id, "SEM_VAGA")
    wait_for_status(multiple_client, second_id, "SEM_VAGA")

    paused = multiple_client.post(f"/api/monitoramentos/{first_id}/pausar")
    second_state = multiple_client.get(f"/api/monitoramentos/{second_id}").json()
    resumed = multiple_client.post(f"/api/monitoramentos/{first_id}/retomar")
    wait_for_status(multiple_client, first_id, "SEM_VAGA")
    removed = multiple_client.delete(f"/api/monitoramentos/{first_id}")

    assert paused.json()["status"] == "PARADO"
    assert second_state["running"] is True
    assert resumed.status_code == 200
    assert removed.json() == {"removed": True, "monitoring_id": first_id}
    assert multiple_client.get(f"/api/monitoramentos/{first_id}").status_code == 404
    assert multiple_client.get(f"/api/monitoramentos/{second_id}").status_code == 200


def test_returns_history_by_monitoring_id(multiple_client: TestClient) -> None:
    monitoring_id = multiple_client.post(
        "/api/monitoramentos", json=payload("1", "2")
    ).json()["monitoring_id"]
    wait_for_status(multiple_client, monitoring_id, "SEM_VAGA")

    response = multiple_client.get(f"/api/monitoramentos/{monitoring_id}/historico")

    assert response.status_code == 200
    assert response.json()["monitoring_id"] == monitoring_id
    assert response.json()["items"][0]["result"] == "SEM_VAGA"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/monitoramentos/999"),
        ("post", "/api/monitoramentos/999/pausar"),
        ("post", "/api/monitoramentos/999/retomar"),
        ("delete", "/api/monitoramentos/999"),
        ("get", "/api/monitoramentos/999/historico"),
    ],
)
def test_returns_not_found_for_unknown_id(
    multiple_client: TestClient,
    method: str,
    path: str,
) -> None:
    response = getattr(multiple_client, method)(path)

    assert response.status_code == 404
