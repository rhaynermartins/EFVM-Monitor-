"""Alertas do monitor, sem qualquer ação de compra."""

from __future__ import annotations

import logging

import httpx

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.network import verified_ssl_context

LOGGER = logging.getLogger(__name__)
PURCHASE_URL = "https://tremdepassageiros.vale.com/sgpweb/portal/index.html#/home"


def send_availability_alert(settings: Settings, result: AvailabilityResult) -> None:
    """Registra o alerta e, se configurado, envia um POST JSON para um webhook."""
    if result.status is not AvailabilityStatus.TEM_VAGA:
        return

    LOGGER.warning(
        "PASSAGEM ENCONTRADA: %s -> %s em %s (%s). Consulte: %s",
        settings.origin,
        settings.destination,
        settings.travel_date.isoformat(),
        settings.travel_class,
        PURCHASE_URL,
    )

    if settings.alert_webhook_url is None:
        return

    payload = {
        "status": result.status.value,
        "message": result.message,
        "origin": settings.origin,
        "destination": settings.destination,
        "travel_date": settings.travel_date.isoformat(),
        "travel_class": settings.travel_class,
        "available_options": result.available_options,
        "purchase_url": PURCHASE_URL,
    }
    with httpx.Client(
        timeout=settings.timeout_seconds,
        follow_redirects=False,
        verify=verified_ssl_context(),
    ) as client:
        response = client.post(settings.alert_webhook_url, json=payload)
        response.raise_for_status()
    LOGGER.info("Alerta enviado ao webhook configurado.")
