"""Entrega de alertas pelo padrão Web Push com credenciais VAPID."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dotenv import load_dotenv
from pywebpush import WebPushException, webpush

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, NotificationStatus, PushSubscription

LOGGER = logging.getLogger(__name__)
WEB_PUSH_CHANNEL = "WEB_PUSH"
PURCHASE_URL = "https://tremdepassageiros.vale.com/sgpweb/portal/index.html#/home"


class WebPushConfigurationError(ValueError):
    """Indica que o canal Web Push não possui configuração VAPID válida."""


class WebPushSendError(RuntimeError):
    """Representa uma entrega Web Push que terminou sem sucesso."""

    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class WebPushConfig:
    enabled: bool
    public_key: str
    private_key: str
    subject: str
    max_attempts: int = 3
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> WebPushConfig:
        load_dotenv()
        return cls(
            enabled=_env_boolean("WEB_PUSH_ENABLED", True),
            public_key=os.getenv("VAPID_PUBLIC_KEY", "").strip(),
            private_key=os.getenv("VAPID_PRIVATE_KEY", "").strip(),
            subject=os.getenv("VAPID_SUBJECT", "").strip(),
            max_attempts=_bounded_integer("WEB_PUSH_MAX_ATTEMPTS", 3, 1, 5),
            timeout_seconds=_bounded_float("WEB_PUSH_TIMEOUT_SECONDS", 15.0, 5.0, 60.0),
        )

    @property
    def configured(self) -> bool:
        return bool(
            self.enabled and self.public_key and self.private_key and self.valid_subject
        )

    @property
    def valid_subject(self) -> bool:
        return self.subject.startswith(("mailto:", "https://"))

    def validate(self) -> None:
        if not self.enabled:
            raise WebPushConfigurationError("Web Push está desabilitado.")
        missing = [
            name
            for name, value in (
                ("VAPID_PUBLIC_KEY", self.public_key),
                ("VAPID_PRIVATE_KEY", self.private_key),
                ("VAPID_SUBJECT", self.subject),
            )
            if not value
        ]
        if missing:
            raise WebPushConfigurationError(
                f"Configuração Web Push incompleta: {', '.join(missing)}."
            )
        if not self.valid_subject:
            raise WebPushConfigurationError(
                "VAPID_SUBJECT deve começar com mailto: ou https://."
            )


class WebPushNotifier:
    """Envia para os dispositivos associados ao monitor, sem interrompê-lo."""

    def __init__(
        self,
        repository: MonitoringRepository,
        config: WebPushConfig,
        *,
        sender: Callable[..., Any] = webpush,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.repository = repository
        self.config = config
        self._sender = sender
        self._sleep = sleep

    def notify(
        self,
        settings: Settings,
        result: AvailabilityResult,
        monitoring_id: int | None,
        detected_at: str,
    ) -> None:
        if (
            result.status is not AvailabilityStatus.TEM_VAGA
            or monitoring_id is None
            or not self.config.configured
        ):
            return
        subscriptions = self.repository.list_push_subscriptions(monitoring_id)
        if not subscriptions:
            return

        payload = availability_payload(settings, detected_at)
        delivery = self.repository.begin_notification(
            monitoring_id,
            detected_at=detected_at,
            result=result.status,
            channel=WEB_PUSH_CHANNEL,
            message=payload["body"],
            provider="Web Push / VAPID",
            recipient_masked=f"{len(subscriptions)} dispositivo(s)",
        )
        if delivery is None:
            LOGGER.info("Alerta Web Push já registrado para esta disponibilidade.")
            return

        successes = 0
        attempts = 0
        errors: list[str] = []
        for subscription in subscriptions:
            try:
                attempts += self._send(subscription, payload)
                successes += 1
            except WebPushSendError as exc:
                attempts += exc.attempts
                errors.append(str(exc))
                LOGGER.warning("Dispositivo Web Push não recebeu o alerta: %s", exc)

        status = NotificationStatus.SENT if successes else NotificationStatus.FAILED
        self.repository.complete_notification(
            delivery.id,
            status=status,
            attempt_count=attempts,
            error_message="; ".join(errors) or None,
            external_message_id=f"{successes}/{len(subscriptions)} dispositivo(s)",
        )

    def send_test(self, device_id: str) -> int:
        self.config.validate()
        subscription = self.repository.get_push_subscription_for_device(device_id)
        if subscription is None:
            raise WebPushSendError("Este dispositivo não possui inscrição ativa.", 0)
        payload = {
            "title": "Teste do EFVM Monitor",
            "body": (
                "Web Push ativado. O sistema apenas alerta sobre disponibilidade; "
                "a compra continua sendo sua responsabilidade."
            ),
            "url": "/",
            "tag": "efvm-web-push-test",
            "icon": "/static/icons/icon-192.png",
            "badge": "/static/icons/badge-96.png",
        }
        return self._send(subscription, payload)

    def _send(self, subscription: PushSubscription, payload: dict[str, Any]) -> int:
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for attempt in range(1, self.config.max_attempts + 1):
            try:
                self._sender(
                    subscription_info=subscription.to_push_info(),
                    data=json.dumps(payload, ensure_ascii=False),
                    vapid_private_key=self.config.private_key,
                    vapid_claims={"sub": self.config.subject},
                    timeout=self.config.timeout_seconds,
                    ttl=300,
                )
                self.repository.mark_push_success(subscription.id, now)
                return attempt
            except WebPushException as exc:
                status_code = getattr(exc.response, "status_code", None)
                expired = status_code in {404, 410}
                retryable = status_code == 429 or (status_code is not None and status_code >= 500)
                self.repository.mark_push_failure(
                    subscription.id,
                    now,
                    deactivate=expired,
                )
                if expired:
                    raise WebPushSendError(
                        "Inscrição expirada e desativada pelo serviço de push.", attempt
                    ) from exc
                if not retryable or attempt == self.config.max_attempts:
                    detail = f"HTTP {status_code}" if status_code is not None else "falha de envio"
                    raise WebPushSendError(f"Web Push não entregue ({detail}).", attempt) from exc
                self._sleep(2 ** (attempt - 1))
            except Exception as exc:
                self.repository.mark_push_failure(subscription.id, now)
                raise WebPushSendError("Web Push não entregue.", attempt) from exc
        raise WebPushSendError("Web Push não entregue.", self.config.max_attempts)


def availability_payload(settings: Settings, detected_at: str) -> dict[str, Any]:
    origin = settings.origin_label or settings.origin
    destination = settings.destination_label or settings.destination
    passenger_label = (
        "1 passageiro" if settings.passengers == 1 else f"{settings.passengers} passageiros"
    )
    detected = datetime.fromisoformat(detected_at).astimezone().strftime("%H:%M")
    return {
        "title": "🚨 Passagem encontrada",
        "body": (
            f"{origin} → {destination} · {settings.travel_date.strftime('%d/%m/%Y')} · "
            f"{settings.travel_class} · {passenger_label}. Detectado às {detected}. "
            "A compra continua sendo sua responsabilidade."
        ),
        "url": PURCHASE_URL,
        "tag": f"efvm-disponibilidade-{settings.origin}-{settings.destination}",
        "icon": "/static/icons/icon-192.png",
        "badge": "/static/icons/badge-96.png",
    }


def _env_boolean(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().casefold()
    if value in {"1", "true", "sim", "yes", "on"}:
        return True
    if value in {"0", "false", "nao", "não", "no", "off"}:
        return False
    raise WebPushConfigurationError(f"{name} deve ser verdadeiro ou falso.")


def _bounded_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise WebPushConfigurationError(f"{name} deve ser um número inteiro.") from exc
    if not minimum <= value <= maximum:
        raise WebPushConfigurationError(f"{name} deve ficar entre {minimum} e {maximum}.")
    return value


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except ValueError as exc:
        raise WebPushConfigurationError(f"{name} deve ser numérico.") from exc
    if not minimum <= value <= maximum:
        raise WebPushConfigurationError(f"{name} deve ficar entre {minimum} e {maximum}.")
    return value
