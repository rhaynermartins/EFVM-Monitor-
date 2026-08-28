from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, NotificationStatus
from efvm_monitor.notifier import (
    NotificationService,
    WhatsAppCloudClient,
    WhatsAppConfig,
    format_whatsapp_message,
)


class FakeHttpClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.payloads: list[dict[str, Any]] = []

    def __enter__(self) -> FakeHttpClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def post(self, url: str, *, json: dict[str, Any]) -> httpx.Response:
        self.payloads.append({"url": url, "json": json})
        return self.responses.pop(0)


def response(status_code: int, body: dict[str, Any]) -> httpx.Response:
    request = httpx.Request("POST", "https://graph.facebook.com/v26.0/123/messages")
    return httpx.Response(status_code, request=request, json=body)


def whatsapp_config(**overrides: Any) -> WhatsAppConfig:
    values = {
        "access_token": "test-token",
        "phone_number_id": "123",
        "recipient_phone": "5531999999999",
        "max_attempts": 3,
        "retry_delay_seconds": 1,
    }
    values.update(overrides)
    return WhatsAppConfig(**values)


def settings() -> Settings:
    return Settings.for_query(
        origin="7185",
        destination="7184",
        travel_date=date.today() + timedelta(days=10),
        travel_class="Econômica",
        passengers=1,
        check_interval_seconds=300,
        whatsapp_enabled=True,
        origin_label="Belo Horizonte",
        destination_label="Dois Irmãos",
    )


def test_formats_message_with_purchase_responsibility() -> None:
    message = format_whatsapp_message(settings(), "2026-08-28T07:11:00-03:00")

    assert "Belo Horizonte → Dois Irmãos" in message
    assert "1 passageiro" in message
    assert "Detectado às 07:11" in message
    assert "a compra continua sendo sua responsabilidade" in message


def test_whatsapp_client_retries_temporary_failure() -> None:
    fake_http = FakeHttpClient(
        [
            response(500, {"error": {"message": "temporary"}}),
            response(200, {"messages": [{"id": "wamid.123"}]}),
        ]
    )
    waits: list[float] = []
    client = WhatsAppCloudClient(
        whatsapp_config(),
        client_factory=lambda: fake_http,
        sleep=waits.append,
    )

    result = client.send("Mensagem", ["um", "dois"])

    assert result.external_message_id == "wamid.123"
    assert result.attempts == 2
    assert waits == [1]
    assert len(fake_http.payloads) == 2
    assert fake_http.payloads[0]["json"]["messaging_product"] == "whatsapp"


def test_service_persists_failure_without_raising(tmp_path: Path) -> None:
    storage = MonitoringRepository(tmp_path / "alerts.db")
    storage.initialize()
    monitor = storage.create_monitor(settings())
    config = whatsapp_config(access_token="", max_attempts=1)
    service = NotificationService(
        repository=storage,
        whatsapp_config=config,
        whatsapp_client=WhatsAppCloudClient(config),
    )

    service.notify(
        settings(),
        AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Disponível.", 1),
        monitor.id,
        "2026-08-28T07:11:00-03:00",
    )

    deliveries = storage.notification_history(monitor.id)
    assert len(deliveries) == 1
    assert deliveries[0].status is NotificationStatus.FAILED
    assert deliveries[0].attempt_count == 0
    assert "WHATSAPP_ACCESS_TOKEN" in (deliveries[0].error_message or "")
