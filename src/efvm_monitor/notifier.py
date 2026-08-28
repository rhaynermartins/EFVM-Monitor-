"""Alertas do monitor, sem qualquer ação de compra."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, NotificationStatus
from efvm_monitor.network import verified_ssl_context

LOGGER = logging.getLogger(__name__)
PURCHASE_URL = "https://tremdepassageiros.vale.com/sgpweb/portal/index.html#/home"
WHATSAPP_CHANNEL = "WHATSAPP"


class WhatsAppConfigurationError(ValueError):
    """Indica que as credenciais necessárias da Cloud API estão incompletas."""


class NotificationSendError(RuntimeError):
    """Representa uma entrega que falhou após a política de tentativas."""

    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class WhatsAppConfig:
    access_token: str
    phone_number_id: str
    recipient_phone: str
    api_version: str = "v26.0"
    template_name: str | None = None
    template_language: str = "pt_BR"
    max_attempts: int = 3
    retry_delay_seconds: float = 1.0
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> WhatsAppConfig:
        load_dotenv()
        return cls(
            access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip(),
            phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip(),
            recipient_phone=os.getenv("WHATSAPP_RECIPIENT_PHONE", "").strip(),
            api_version=os.getenv("WHATSAPP_API_VERSION", "v26.0").strip() or "v26.0",
            template_name=os.getenv("WHATSAPP_TEMPLATE_NAME", "").strip() or None,
            template_language=(
                os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "pt_BR").strip() or "pt_BR"
            ),
            max_attempts=_bounded_integer("WHATSAPP_MAX_ATTEMPTS", 3, 1, 5),
            retry_delay_seconds=_bounded_float(
                "WHATSAPP_RETRY_DELAY_SECONDS", 1.0, 0.0, 30.0
            ),
            timeout_seconds=_bounded_float("WHATSAPP_TIMEOUT_SECONDS", 30.0, 5.0, 120.0),
        )

    @property
    def configured(self) -> bool:
        return bool(self.access_token and self.phone_number_id and self.recipient_phone)

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("WHATSAPP_ACCESS_TOKEN", self.access_token),
                ("WHATSAPP_PHONE_NUMBER_ID", self.phone_number_id),
                ("WHATSAPP_RECIPIENT_PHONE", self.recipient_phone),
            )
            if not value
        ]
        if missing:
            raise WhatsAppConfigurationError(
                f"Configuração do WhatsApp incompleta: {', '.join(missing)}."
            )


@dataclass(frozen=True, slots=True)
class SendResult:
    external_message_id: str | None
    attempts: int


class WhatsAppCloudClient:
    """Envia mensagens somente pela API oficial da Meta, sem WhatsApp Web."""

    def __init__(
        self,
        config: WhatsAppConfig,
        *,
        client_factory: Callable[[], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self._client_factory = client_factory or self._default_client
        self._sleep = sleep

    def send(self, message: str, template_parameters: list[str]) -> SendResult:
        self.config.validate()
        url = (
            f"https://graph.facebook.com/{self.config.api_version}/"
            f"{self.config.phone_number_id}/messages"
        )
        payload = self._payload(message, template_parameters)
        last_error = "Falha desconhecida na Cloud API."

        with self._client_factory() as client:
            for attempt in range(1, self.config.max_attempts + 1):
                try:
                    response = client.post(url, json=payload)
                    response.raise_for_status()
                    try:
                        body = response.json()
                    except ValueError:
                        body = {}
                    messages = body.get("messages", [])
                    external_id = messages[0].get("id") if messages else None
                    return SendResult(external_message_id=external_id, attempts=attempt)
                except httpx.HTTPStatusError as exc:
                    last_error = f"Cloud API respondeu HTTP {exc.response.status_code}."
                    retryable = exc.response.status_code == 429 or exc.response.status_code >= 500
                    if not retryable or attempt == self.config.max_attempts:
                        raise NotificationSendError(last_error, attempt) from exc
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    last_error = f"Não foi possível concluir o envio: {exc}."
                    if attempt == self.config.max_attempts:
                        raise NotificationSendError(last_error, attempt) from exc
                except httpx.RequestError as exc:
                    raise NotificationSendError(
                        "A conexão foi interrompida após iniciar o envio; "
                        "a tentativa não será repetida para evitar mensagem duplicada.",
                        attempt,
                    ) from exc
                self._sleep(self.config.retry_delay_seconds * (2 ** (attempt - 1)))

        raise NotificationSendError(last_error, self.config.max_attempts)

    def _default_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
            verify=verified_ssl_context(),
            headers={
                "Authorization": f"Bearer {self.config.access_token}",
                "Content-Type": "application/json",
            },
        )

    def _payload(self, message: str, template_parameters: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.config.recipient_phone,
        }
        if self.config.template_name:
            payload.update(
                {
                    "type": "template",
                    "template": {
                        "name": self.config.template_name,
                        "language": {"code": self.config.template_language},
                        "components": [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": value}
                                    for value in template_parameters
                                ],
                            }
                        ],
                    },
                }
            )
        else:
            payload.update(
                {
                    "type": "text",
                    "text": {"preview_url": False, "body": message},
                }
            )
        return payload


class NotificationService:
    """Coordena canais de alerta e garante que falhas não parem o monitor."""

    def __init__(
        self,
        *,
        repository: MonitoringRepository | None = None,
        whatsapp_client: WhatsAppCloudClient | None = None,
        whatsapp_config: WhatsAppConfig | None = None,
    ) -> None:
        self._repository = repository
        self.whatsapp_config = whatsapp_config or WhatsAppConfig.from_env()
        self._whatsapp = whatsapp_client or WhatsAppCloudClient(self.whatsapp_config)

    @property
    def whatsapp_configured(self) -> bool:
        return self.whatsapp_config.configured

    def notify(
        self,
        settings: Settings,
        result: AvailabilityResult,
        monitoring_id: int | None,
        detected_at: str,
    ) -> None:
        if result.status is not AvailabilityStatus.TEM_VAGA:
            return
        if settings.whatsapp_enabled:
            try:
                self._notify_whatsapp(settings, result, monitoring_id, detected_at)
            except Exception as exc:
                LOGGER.exception("O alerta do WhatsApp não pôde ser processado: %s", exc)
        try:
            send_availability_alert(settings, result)
        except httpx.HTTPError as exc:
            LOGGER.error("O webhook complementar não recebeu o alerta: %s", exc)

    def send_test(self) -> SendResult:
        detected_at = datetime.now().astimezone().isoformat(timespec="seconds")
        message = (
            "✅ TESTE DE ALERTA EFVM\n\n"
            "A integração com o WhatsApp Cloud API está configurada.\n\n"
            f"Enviado às {_format_time(detected_at)}.\n"
            "Este sistema apenas monitora disponibilidade e não realiza compras."
        )
        return self._whatsapp.send(
            message,
            ["Teste", "EFVM", "—", "—", "1 passageiro", _format_time(detected_at), PURCHASE_URL],
        )

    def _notify_whatsapp(
        self,
        settings: Settings,
        result: AvailabilityResult,
        monitoring_id: int | None,
        detected_at: str,
    ) -> None:
        message = format_whatsapp_message(settings, detected_at)
        delivery_id: int | None = None
        if self._repository is not None and monitoring_id is not None:
            delivery = self._repository.begin_notification(
                monitoring_id,
                detected_at=detected_at,
                result=result.status,
                channel=WHATSAPP_CHANNEL,
                message=message,
            )
            if delivery is None:
                LOGGER.info("Alerta do WhatsApp já registrado para esta disponibilidade.")
                return
            delivery_id = delivery.id

        parameters = _template_parameters(settings, detected_at)
        try:
            send_result = self._whatsapp.send(message, parameters)
        except (NotificationSendError, WhatsAppConfigurationError) as exc:
            attempts = exc.attempts if isinstance(exc, NotificationSendError) else 0
            if self._repository is not None and delivery_id is not None:
                self._repository.complete_notification(
                    delivery_id,
                    status=NotificationStatus.FAILED,
                    attempt_count=attempts,
                    error_message=str(exc),
                )
            LOGGER.error("Alerta do WhatsApp não enviado: %s", exc)
            return

        if self._repository is not None and delivery_id is not None:
            self._repository.complete_notification(
                delivery_id,
                status=NotificationStatus.SENT,
                attempt_count=send_result.attempts,
                external_message_id=send_result.external_message_id,
            )
        LOGGER.info("Alerta enviado pelo WhatsApp Cloud API.")


def format_whatsapp_message(settings: Settings, detected_at: str) -> str:
    origin = settings.origin_label or settings.origin
    destination = settings.destination_label or settings.destination
    passenger_label = (
        "1 passageiro"
        if settings.passengers == 1
        else f"{settings.passengers} passageiros"
    )
    return (
        "🚨 PASSAGEM ENCONTRADA\n\n"
        f"🚆 {origin} → {destination}\n"
        f"📅 {settings.travel_date.strftime('%d/%m/%Y')}\n"
        f"💺 {settings.travel_class}\n"
        f"👤 {passenger_label}\n\n"
        "Foi encontrada disponibilidade de passagem.\n\n"
        f"Detectado às {_format_time(detected_at)}.\n\n"
        "Acesse o portal oficial da Vale para realizar a compra.\n"
        f"{PURCHASE_URL}\n\n"
        "O sistema apenas encontrou disponibilidade; a compra continua sendo sua responsabilidade."
    )


def _template_parameters(settings: Settings, detected_at: str) -> list[str]:
    return [
        settings.origin_label or settings.origin,
        settings.destination_label or settings.destination,
        settings.travel_date.strftime("%d/%m/%Y"),
        settings.travel_class,
        "1 passageiro" if settings.passengers == 1 else f"{settings.passengers} passageiros",
        _format_time(detected_at),
        PURCHASE_URL,
    ]


def _format_time(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%H:%M")


def _bounded_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise WhatsAppConfigurationError(f"{name} deve ser um número inteiro.") from exc
    if not minimum <= value <= maximum:
        raise WhatsAppConfigurationError(f"{name} deve ficar entre {minimum} e {maximum}.")
    return value


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise WhatsAppConfigurationError(f"{name} deve ser numérico.") from exc
    if not minimum <= value <= maximum:
        raise WhatsAppConfigurationError(f"{name} deve ficar entre {minimum} e {maximum}.")
    return value


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
