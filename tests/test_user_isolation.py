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
from efvm_monitor.notifier import NotificationService
from efvm_monitor.web import create_app
from efvm_monitor.web_push import WebPushConfig, WebPushNotifier

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
    def __init__(self, _settings: Settings) -> None:
        pass

    def __enter__(self) -> ImmediateClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def check(self) -> AvailabilityResult:
        return AvailabilityResult(AvailabilityStatus.SEM_VAGA, "Ainda não há passagens.")


@pytest.fixture
def isolated_client(tmp_path: Path) -> Iterator[TestClient]:
    storage = MonitoringRepository(tmp_path / "isolated.db")
    manager = MonitoringManager(
        storage,
        client_factory=ImmediateClient,
        notifier=lambda *_: None,
    )
    push_config = WebPushConfig(
        enabled=True,
        public_key="public-browser-key",
        private_key="private-server-key",
        subject="mailto:responsavel@example.com",
    )
    web_push = WebPushNotifier(storage, push_config, sender=lambda **_: None)
    notifications = NotificationService(
        repository=storage,
        web_push_config=push_config,
        web_push_notifier=web_push,
    )
    application = create_app(
        manager=manager,
        repository=storage,
        catalog_provider=lambda: CATALOG,
        notification_service=notifications,
    )
    with TestClient(application) as client:
        yield client


def create_account(client: TestClient, name: str, email: str) -> tuple[str, str]:
    response = client.post(
        "/api/auth/cadastro",
        json={"name": name, "email": email, "password": "senha-segura-123"},
    )
    assert response.status_code == 201
    cookie = client.cookies.get("efvm_session")
    assert cookie
    csrf = response.json()["csrf_token"]
    client.cookies.clear()
    return cookie, csrf


def use_account(client: TestClient, account: tuple[str, str]) -> dict[str, str]:
    cookie, csrf = account
    client.cookies.clear()
    client.cookies.set("efvm_session", cookie)
    return {"X-CSRF-Token": csrf}


def monitor_payload(origin: str, destination: str) -> dict[str, Any]:
    return {
        "origin": origin,
        "destination": destination,
        "travel_date": (date.today() + timedelta(days=10)).isoformat(),
        "travel_class": "Econômica",
        "passengers": 1,
        "interval_seconds": 300,
        "whatsapp_enabled": False,
        "sms_enabled": False,
    }


def wait_for_history(client: TestClient, monitoring_id: int) -> None:
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        response = client.get(f"/api/monitoramentos/{monitoring_id}/historico")
        if response.status_code == 200 and response.json()["items"]:
            return
        time.sleep(0.01)
    raise AssertionError("O histórico do monitor não foi preenchido.")


def subscription(device_id: str, suffix: str) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "endpoint": f"https://push.example.test/{suffix}",
        "keys": {"p256dh": f"p256dh-{suffix}-value", "auth": f"auth-{suffix}-value"},
    }


def test_isolates_monitor_lists_and_all_id_routes(isolated_client: TestClient) -> None:
    client = isolated_client
    account_a = create_account(client, "Usuário A", "a@example.com")
    account_b = create_account(client, "Usuário B", "b@example.com")

    headers_a = use_account(client, account_a)
    monitor_a = client.post(
        "/api/monitoramentos",
        json=monitor_payload("1", "2"),
        headers=headers_a,
    ).json()["monitoring_id"]
    wait_for_history(client, monitor_a)

    headers_b = use_account(client, account_b)
    monitor_b = client.post(
        "/api/monitoramentos",
        json=monitor_payload("2", "3"),
        headers=headers_b,
    ).json()["monitoring_id"]
    wait_for_history(client, monitor_b)

    listed_b = client.get("/api/monitoramentos").json()["items"]
    assert [item["monitoring_id"] for item in listed_b] == [monitor_b]

    forbidden_requests = [
        client.get(f"/api/monitoramentos/{monitor_a}"),
        client.post(f"/api/monitoramentos/{monitor_a}/pausar", headers=headers_b),
        client.post(f"/api/monitoramentos/{monitor_a}/retomar", headers=headers_b),
        client.delete(f"/api/monitoramentos/{monitor_a}", headers=headers_b),
        client.get(f"/api/monitoramentos/{monitor_a}/historico"),
    ]
    assert {response.status_code for response in forbidden_requests} == {404}

    use_account(client, account_a)
    listed_a = client.get("/api/monitoramentos").json()["items"]
    assert [item["monitoring_id"] for item in listed_a] == [monitor_a]


def test_isolates_web_push_devices_between_users(isolated_client: TestClient) -> None:
    client = isolated_client
    account_a = create_account(client, "Usuário A", "push-a@example.com")
    account_b = create_account(client, "Usuário B", "push-b@example.com")
    device_a = subscription("device-user-a-1234", "subscription-a")
    device_b = subscription("device-user-b-1234", "subscription-b")

    headers_a = use_account(client, account_a)
    assert client.post("/api/push/subscribe", json=device_a, headers=headers_a).status_code == 201
    headers_b = use_account(client, account_b)
    assert client.post("/api/push/subscribe", json=device_b, headers=headers_b).status_code == 201

    foreign_status = client.get(
        "/api/push/status",
        params={"device_id": device_a["device_id"]},
    )
    foreign_test = client.post(
        "/api/push/test",
        json={"device_id": device_a["device_id"]},
        headers=headers_b,
    )
    own_status = client.get(
        "/api/push/status",
        params={"device_id": device_b["device_id"]},
    )

    assert foreign_status.json()["subscribed"] is False
    assert foreign_test.status_code == 502
    assert own_status.json()["subscribed"] is True


def test_transfers_reconfirmed_push_endpoint_without_cross_account_access(
    isolated_client: TestClient,
) -> None:
    client = isolated_client
    account_a = create_account(client, "Usuário A", "shared-a@example.com")
    account_b = create_account(client, "Usuário B", "shared-b@example.com")
    shared_device = subscription("shared-device-1234", "shared-subscription")

    headers_a = use_account(client, account_a)
    first = client.post(
        "/api/push/subscribe",
        json=shared_device,
        headers=headers_a,
    )
    headers_b = use_account(client, account_b)
    transferred = client.post(
        "/api/push/subscribe",
        json=shared_device,
        headers=headers_b,
    )
    current_owner = client.get(
        "/api/push/status",
        params={"device_id": shared_device["device_id"]},
    )
    use_account(client, account_a)
    previous_owner = client.get(
        "/api/push/status",
        params={"device_id": shared_device["device_id"]},
    )

    assert first.status_code == 201
    assert transferred.status_code == 201
    assert current_owner.json()["subscribed"] is True
    assert previous_owner.json()["subscribed"] is False
