from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pywebpush import WebPushException

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, NotificationStatus
from efvm_monitor.web_push import WebPushConfig, WebPushNotifier, availability_payload


def settings() -> Settings:
    return Settings.for_query(
        origin="7185",
        destination="7184",
        travel_date=date.today() + timedelta(days=10),
        travel_class="Econômica",
        passengers=1,
        check_interval_seconds=300,
        origin_label="Belo Horizonte",
        destination_label="Dois Irmãos",
    )


def configured(**overrides: Any) -> WebPushConfig:
    values = {
        "enabled": True,
        "public_key": "public-test-key",
        "private_key": "private-test-key",
        "subject": "mailto:responsavel@example.com",
        "max_attempts": 3,
        "timeout_seconds": 10,
    }
    values.update(overrides)
    return WebPushConfig(**values)


def repository(tmp_path: Path) -> MonitoringRepository:
    storage = MonitoringRepository(tmp_path / "web-push.db")
    storage.initialize()
    return storage


def add_subscription(storage: MonitoringRepository, monitoring_id: int):
    subscription = storage.upsert_push_subscription(
        device_id="device-1234567890",
        endpoint="https://push.example.test/subscription-1",
        p256dh="p256dh-test-value",
        auth="auth-test-value",
        user_agent="Test Browser",
    )
    storage.link_push_subscription(monitoring_id, subscription.id)
    return subscription


def test_subscription_is_idempotent_linked_and_unsubscribed(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    monitor = storage.create_monitor(settings())
    first = add_subscription(storage, monitor.id)
    duplicate = add_subscription(storage, monitor.id)

    assert duplicate.id == first.id
    assert len(storage.list_push_subscriptions(monitor.id)) == 1

    deactivated = storage.unsubscribe_push(
        device_id=first.device_id,
        endpoint=first.endpoint,
    )

    assert deactivated is True
    assert storage.list_push_subscriptions(monitor.id) == []
    assert storage.get_push_subscription_for_device(first.device_id) is None


def test_notification_persists_delivery_without_private_key_in_payload(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    monitor = storage.create_monitor(settings())
    add_subscription(storage, monitor.id)
    calls: list[dict[str, Any]] = []

    notifier = WebPushNotifier(
        storage,
        configured(),
        sender=lambda **kwargs: calls.append(kwargs),
    )
    notifier.notify(
        settings(),
        AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Disponível.", 1),
        monitor.id,
        "2026-08-28T07:11:00-03:00",
    )
    notifier.notify(
        settings(),
        AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Disponível.", 1),
        monitor.id,
        "2026-08-28T07:11:00-03:00",
    )

    payload = json.loads(calls[0]["data"])
    deliveries = storage.notification_history(monitor.id)
    assert len(calls) == 1
    assert len(deliveries) == 1
    assert deliveries[0].channel == "WEB_PUSH"
    assert deliveries[0].status is NotificationStatus.SENT
    assert "private-test-key" not in json.dumps(payload)
    assert "a compra continua sendo sua responsabilidade" in payload["body"].casefold()


def test_disabled_channel_and_non_available_result_do_not_send(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    monitor = storage.create_monitor(settings())
    add_subscription(storage, monitor.id)
    calls: list[dict[str, Any]] = []
    notifier = WebPushNotifier(
        storage,
        configured(enabled=False),
        sender=lambda **kwargs: calls.append(kwargs),
    )

    notifier.notify(
        settings(),
        AvailabilityResult(AvailabilityStatus.SEM_VAGA, "Indisponível.", 0),
        monitor.id,
        "2026-08-28T07:11:00-03:00",
    )
    notifier.notify(
        settings(),
        AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Disponível.", 1),
        monitor.id,
        "2026-08-28T07:12:00-03:00",
    )

    assert calls == []
    assert storage.notification_history(monitor.id) == []


def test_temporary_failure_retries_and_monitor_flow_does_not_receive_exception(
    tmp_path: Path,
) -> None:
    storage = repository(tmp_path)
    monitor = storage.create_monitor(settings())
    add_subscription(storage, monitor.id)
    attempts = 0
    waits: list[float] = []

    def sender(**_: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            response = SimpleNamespace(status_code=503, text="temporário")
            raise WebPushException("temporário", response=response)

    notifier = WebPushNotifier(storage, configured(), sender=sender, sleep=waits.append)
    notifier.notify(
        settings(),
        AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Disponível.", 1),
        monitor.id,
        "2026-08-28T07:13:00-03:00",
    )

    delivery = storage.notification_history(monitor.id)[0]
    assert attempts == 3
    assert waits == [1, 2]
    assert delivery.status is NotificationStatus.SENT
    assert delivery.attempt_count == 3


def test_expired_subscription_is_deactivated_without_raising(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    monitor = storage.create_monitor(settings())
    subscription = add_subscription(storage, monitor.id)

    def expired(**_: Any) -> None:
        response = SimpleNamespace(status_code=410, text="gone")
        raise WebPushException("gone", response=response)

    notifier = WebPushNotifier(storage, configured(), sender=expired)
    notifier.notify(
        settings(),
        AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Disponível.", 1),
        monitor.id,
        "2026-08-28T07:14:00-03:00",
    )

    delivery = storage.notification_history(monitor.id)[0]
    assert storage.get_push_subscription_for_device(subscription.device_id) is None
    assert delivery.status is NotificationStatus.FAILED
    assert "expirada" in (delivery.error_message or "")


def test_payload_contains_route_class_time_and_official_link() -> None:
    payload = availability_payload(settings(), "2026-08-28T07:11:00-03:00")

    assert "Belo Horizonte → Dois Irmãos" in payload["body"]
    assert "Econômica" in payload["body"]
    assert "07:11" in payload["body"]
    assert payload["url"].startswith("https://tremdepassageiros.vale.com/")
