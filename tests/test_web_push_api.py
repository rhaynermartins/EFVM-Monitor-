from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository
from efvm_monitor.monitor import MonitorSnapshot
from efvm_monitor.notifier import NotificationService
from efvm_monitor.web import create_app
from efvm_monitor.web_push import WebPushConfig, WebPushNotifier


class QuietMonitor:
    def start(self, _settings: Settings, user_id: int = 1) -> MonitorSnapshot:
        assert user_id == 1
        return MonitorSnapshot(running=True, status="AGUARDANDO")

    def stop(self) -> MonitorSnapshot:
        return MonitorSnapshot()

    def snapshot(self) -> MonitorSnapshot:
        return MonitorSnapshot()

    def history(self, _limit: int = 100) -> list[dict[str, Any]]:
        return []

    def restore(self, *_: Any) -> MonitorSnapshot:
        return MonitorSnapshot()

    def shutdown(self) -> None:
        return None


@pytest.fixture
def push_client(tmp_path: Path) -> Iterator[tuple[TestClient, list[dict[str, Any]]]]:
    storage = MonitoringRepository(tmp_path / "push-api.db")
    storage.initialize()
    config = WebPushConfig(
        enabled=True,
        public_key="public-browser-key",
        private_key="private-server-key",
        subject="mailto:responsavel@example.com",
    )
    calls: list[dict[str, Any]] = []
    push_notifier = WebPushNotifier(storage, config, sender=lambda **kwargs: calls.append(kwargs))
    notification_service = NotificationService(
        repository=storage,
        web_push_config=config,
        web_push_notifier=push_notifier,
    )
    application = create_app(
        monitor=QuietMonitor(),
        repository=storage,
        catalog_provider=lambda: {"stations": [], "classes": [], "sale_window_days": 45},
        notification_service=notification_service,
        authentication_enabled=False,
    )
    with TestClient(application) as client:
        client.headers["X-CSRF-Token"] = "test-csrf-token"
        yield client, calls


def subscription_payload() -> dict[str, Any]:
    return {
        "device_id": "device-1234567890",
        "endpoint": "https://push.example.test/subscription-1",
        "keys": {
            "p256dh": "p256dh-test-value",
            "auth": "auth-test-value",
        },
    }


def test_public_config_never_exposes_private_key(
    push_client: tuple[TestClient, list[dict[str, Any]]],
) -> None:
    client, _ = push_client

    response = client.get("/api/push/config")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": True,
        "configured": True,
        "public_key": "public-browser-key",
    }
    assert "private-server-key" not in response.text


def test_subscribe_status_test_and_unsubscribe_flow(
    push_client: tuple[TestClient, list[dict[str, Any]]],
) -> None:
    client, calls = push_client
    payload = subscription_payload()

    first = client.post("/api/push/subscribe", json=payload)
    duplicate = client.post("/api/push/subscribe", json=payload)
    active_status = client.get("/api/push/status", params={"device_id": payload["device_id"]})
    test_response = client.post(
        "/api/push/test",
        json={"device_id": payload["device_id"]},
    )
    unsubscribe = client.post(
        "/api/push/unsubscribe",
        json={"device_id": payload["device_id"], "endpoint": payload["endpoint"]},
    )
    inactive_status = client.get(
        "/api/push/status",
        params={"device_id": payload["device_id"]},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert active_status.json()["subscribed"] is True
    assert test_response.json() == {"sent": True, "attempts": 1}
    assert len(calls) == 1
    assert unsubscribe.json() == {"subscribed": False, "deactivated": True}
    assert inactive_status.json()["subscribed"] is False


def test_rejects_non_https_push_endpoint(
    push_client: tuple[TestClient, list[dict[str, Any]]],
) -> None:
    client, _ = push_client
    payload = subscription_payload()
    payload["endpoint"] = "http://push.example.test/subscription-1"

    response = client.post("/api/push/subscribe", json=payload)

    assert response.status_code == 422


def test_serves_manifest_and_root_scoped_service_worker(
    push_client: tuple[TestClient, list[dict[str, Any]]],
) -> None:
    client, _ = push_client

    manifest = client.get("/manifest.webmanifest")
    service_worker = client.get("/service-worker.js")

    assert manifest.status_code == 200
    assert manifest.json()["display"] == "standalone"
    assert service_worker.status_code == 200
    assert service_worker.headers["service-worker-allowed"] == "/"
    assert "notificationclick" in service_worker.text
