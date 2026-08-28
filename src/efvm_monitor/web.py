"""Aplicação web local do monitor EFVM."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from efvm_monitor.checker import EFVMClient, PortalError
from efvm_monitor.cli import _configure_logging
from efvm_monitor.config import ConfigurationError, Settings
from efvm_monitor.database import MonitoringRepository, PersistedMonitor
from efvm_monitor.manager import MonitoringManager, MonitoringNotFound
from efvm_monitor.monitor import MonitorAlreadyRunning, MonitorService, MonitorSnapshot
from efvm_monitor.notifier import NotificationService
from efvm_monitor.web_push import WebPushConfigurationError, WebPushSendError

LOGGER = logging.getLogger(__name__)
PACKAGE_DIRECTORY = Path(__file__).parent
templates = Jinja2Templates(directory=PACKAGE_DIRECTORY / "templates")


class MonitoringRequest(BaseModel):
    origin: str = Field(min_length=1)
    destination: str = Field(min_length=1)
    travel_date: date
    travel_class: Literal["Econômica", "Executiva"]
    passengers: Literal[1] = 1
    interval_seconds: int = Field(default=300, ge=60, le=86_400)
    whatsapp_enabled: bool = False
    sms_enabled: bool = False
    push_device_id: str | None = Field(default=None, min_length=16, max_length=128)


class PushKeysRequest(BaseModel):
    p256dh: str = Field(min_length=16, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionRequest(BaseModel):
    device_id: str = Field(min_length=16, max_length=128)
    endpoint: str = Field(min_length=16, max_length=2_048)
    keys: PushKeysRequest

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("O endpoint Web Push deve usar HTTPS.")
        return value


class PushUnsubscribeRequest(BaseModel):
    device_id: str = Field(min_length=16, max_length=128)
    endpoint: str = Field(min_length=16, max_length=2_048)


class PushDeviceRequest(BaseModel):
    device_id: str = Field(min_length=16, max_length=128)


class CatalogCache:
    """Evita repetir as consultas de catálogo a cada abertura da tela."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl_seconds = ttl_seconds
        self._value: dict[str, Any] | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self) -> dict[str, Any]:
        with self._lock:
            if self._value is not None and time.monotonic() < self._expires_at:
                return self._value

            settings = _settings_for_query(
                origin="catalog-origin",
                destination="catalog-destination",
                travel_date=date.today() + timedelta(days=1),
                travel_class="Econômica",
                interval_seconds=300,
            )
            with EFVMClient(settings) as client:
                self._value = client.get_public_catalog()
            self._expires_at = time.monotonic() + self._ttl_seconds
            return self._value


def _settings_for_query(
    *,
    origin: str,
    destination: str,
    travel_date: date,
    travel_class: str,
    interval_seconds: int,
    whatsapp_enabled: bool = False,
    origin_label: str | None = None,
    destination_label: str | None = None,
    sms_enabled: bool = False,
) -> Settings:
    return Settings.for_query(
        origin=origin,
        destination=destination,
        travel_date=travel_date,
        travel_class=travel_class,
        passengers=1,
        check_interval_seconds=interval_seconds,
        timeout_seconds=int(os.getenv("EFVM_TIMEOUT_SECONDS", "30")),
        log_level=os.getenv("EFVM_LOG_LEVEL", "INFO"),
        base_url=os.getenv(
            "EFVM_BASE_URL", "https://tremdepassageiros.vale.com/sgpweb/rest"
        ),
        railway_code=os.getenv("EFVM_RAILWAY_CODE", "03"),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL"),
        whatsapp_enabled=whatsapp_enabled,
        origin_label=origin_label,
        destination_label=destination_label,
        sms_enabled=sms_enabled,
    )


def _settings_from_monitor(monitor: PersistedMonitor) -> Settings:
    return monitor.to_settings(
        timeout_seconds=int(os.getenv("EFVM_TIMEOUT_SECONDS", "30")),
        log_level=os.getenv("EFVM_LOG_LEVEL", "INFO"),
        base_url=os.getenv(
            "EFVM_BASE_URL", "https://tremdepassageiros.vale.com/sgpweb/rest"
        ),
        railway_code=os.getenv("EFVM_RAILWAY_CODE", "03"),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL"),
    )


def create_app(
    *,
    monitor: MonitorService | None = None,
    manager: MonitoringManager | None = None,
    repository: MonitoringRepository | None = None,
    catalog_provider: Callable[[], dict[str, Any]] | None = None,
    notification_service: NotificationService | None = None,
) -> FastAPI:
    storage = repository or (manager.repository if manager is not None else None)
    if storage is None and monitor is None:
        storage = MonitoringRepository(
            os.getenv("EFVM_DATABASE_PATH", "data/efvm-monitor.db")
        )
    notifications = notification_service or NotificationService(repository=storage)
    manager_service = manager
    monitor_service = monitor
    if manager_service is None and monitor_service is None:
        if storage is None:
            raise RuntimeError("A persistência é obrigatória para múltiplos monitoramentos.")
        manager_service = MonitoringManager(
            storage,
            notifier=notifications.notify,
        )
    provide_catalog = catalog_provider or CatalogCache().get

    def snapshots() -> list[MonitorSnapshot]:
        if manager_service is not None:
            return manager_service.list()
        if monitor_service is None:
            return []
        snapshot = monitor_service.snapshot()
        return [snapshot] if snapshot.monitoring_id is not None or snapshot.query else []

    def latest_snapshot() -> MonitorSnapshot:
        current = snapshots()
        return current[0] if current else MonitorSnapshot()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if storage is not None:
            storage.initialize()
            if manager_service is not None:
                manager_service.restore_all(_settings_from_monitor)
            else:
                saved_monitor = storage.latest_monitor()
                saved_settings: Settings | None = None
                if saved_monitor is not None and saved_monitor.active:
                    try:
                        saved_settings = _settings_from_monitor(saved_monitor)
                    except ConfigurationError as exc:
                        LOGGER.error("Monitor salvo não pôde ser retomado: %s", exc)
                if saved_monitor is not None and monitor_service is not None:
                    monitor_service.restore(saved_monitor, saved_settings)
        yield
        if manager_service is not None:
            manager_service.shutdown()
        elif monitor_service is not None:
            monitor_service.shutdown()

    application = FastAPI(
        title="EFVM Monitor",
        description="Interface local de consulta de disponibilidade, sem compra de passagem.",
        version="0.4.2",
        lifespan=lifespan,
    )
    application.mount(
        "/static",
        StaticFiles(directory=PACKAGE_DIRECTORY / "static"),
        name="static",
    )

    @application.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "minimum_date": (date.today() + timedelta(days=1)).isoformat(),
                "official_portal_url": (
                    "https://tremdepassageiros.vale.com/sgpweb/portal/index.html#/home"
                ),
                "whatsapp_configured": notifications.whatsapp_configured,
                "sms_configured": notifications.sms_configured,
                "sms_recipient_masked": notifications.sms_recipient_masked,
            },
        )

    @application.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            PACKAGE_DIRECTORY / "static" / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @application.get("/service-worker.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(
            PACKAGE_DIRECTORY / "static" / "service-worker.js",
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )

    @application.get("/api/push/config")
    def push_config() -> dict[str, Any]:
        return {
            "enabled": notifications.web_push_config.enabled,
            "configured": notifications.web_push_configured,
            "public_key": notifications.web_push_public_key,
        }

    @application.get("/api/push/status")
    def push_status(device_id: str = Query(min_length=16, max_length=128)) -> dict[str, Any]:
        subscription = (
            storage.get_push_subscription_for_device(device_id)
            if storage is not None
            else None
        )
        return {
            "enabled": notifications.web_push_config.enabled,
            "configured": notifications.web_push_configured,
            "subscribed": subscription is not None,
            "last_success_at": subscription.last_success_at if subscription else None,
            "last_failure_at": subscription.last_failure_at if subscription else None,
        }

    @application.post("/api/push/subscribe", status_code=status.HTTP_201_CREATED)
    def subscribe_push(payload: PushSubscriptionRequest, request: Request) -> dict[str, Any]:
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Persistência Web Push indisponível.",
            )
        if not notifications.web_push_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Web Push ainda não foi configurado no servidor.",
            )
        subscription = storage.upsert_push_subscription(
            device_id=payload.device_id,
            endpoint=payload.endpoint,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            user_agent=request.headers.get("user-agent"),
        )
        monitoring_ids = [
            item.monitoring_id for item in snapshots() if item.monitoring_id is not None
        ]
        for monitoring_id in monitoring_ids:
            storage.link_push_subscription(monitoring_id, subscription.id)
        return {
            "subscribed": True,
            "linked_monitoring_id": monitoring_ids[0] if monitoring_ids else None,
            "linked_monitoring_ids": monitoring_ids,
        }

    @application.post("/api/push/unsubscribe")
    def unsubscribe_push(payload: PushUnsubscribeRequest) -> dict[str, Any]:
        if storage is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Persistência Web Push indisponível.",
            )
        deactivated = storage.unsubscribe_push(
            device_id=payload.device_id,
            endpoint=payload.endpoint,
        )
        return {"subscribed": False, "deactivated": deactivated}

    @application.post("/api/push/test")
    def test_push(payload: PushDeviceRequest) -> dict[str, Any]:
        try:
            attempts = notifications.send_test_web_push(payload.device_id)
        except (WebPushConfigurationError, WebPushSendError) as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        return {"sent": True, "attempts": attempts}

    @application.get("/api/catalogo")
    def catalog() -> dict[str, Any]:
        try:
            return provide_catalog()
        except (httpx.HTTPError, PortalError, ConfigurationError, ValueError) as exc:
            LOGGER.error("Catálogo indisponível: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Não foi possível carregar o catálogo: {exc}",
            ) from exc

    @application.post("/api/monitoramento", status_code=status.HTTP_202_ACCEPTED)
    def start_monitoring(payload: MonitoringRequest) -> dict[str, Any]:
        return _create_monitoring(payload)

    def _create_monitoring(payload: MonitoringRequest) -> dict[str, Any]:
        try:
            labels = _station_labels(provide_catalog, payload.origin, payload.destination)
            settings = _settings_for_query(
                origin=payload.origin,
                destination=payload.destination,
                travel_date=payload.travel_date,
                travel_class=payload.travel_class,
                interval_seconds=payload.interval_seconds,
                whatsapp_enabled=payload.whatsapp_enabled,
                origin_label=labels[0],
                destination_label=labels[1],
                sms_enabled=payload.sms_enabled,
            )
            if manager_service is not None:
                snapshot = manager_service.create(settings)
            elif monitor_service is not None:
                snapshot = monitor_service.start(settings)
            else:
                raise RuntimeError("Serviço de monitoramento indisponível.")
            if storage is not None and payload.push_device_id and snapshot.monitoring_id:
                subscription = storage.get_push_subscription_for_device(payload.push_device_id)
                if subscription is not None:
                    storage.link_push_subscription(snapshot.monitoring_id, subscription.id)
            return snapshot.to_dict()
        except ConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except MonitorAlreadyRunning as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @application.get("/api/monitoramento")
    def monitoring_status() -> dict[str, Any]:
        return latest_snapshot().to_dict()

    @application.get("/api/monitoramento/historico")
    def monitoring_history(
        limit: int = Query(default=50, ge=1, le=1_000),
    ) -> dict[str, Any]:
        snapshot = latest_snapshot()
        return {
            "monitoring_id": snapshot.monitoring_id,
            "items": (
                manager_service.history(snapshot.monitoring_id, limit)
                if manager_service is not None and snapshot.monitoring_id is not None
                else monitor_service.history(limit) if monitor_service is not None else []
            ),
        }

    @application.delete("/api/monitoramento")
    def stop_monitoring() -> dict[str, Any]:
        snapshot = latest_snapshot()
        if manager_service is not None and snapshot.monitoring_id is not None:
            return manager_service.pause(snapshot.monitoring_id).to_dict()
        if monitor_service is not None:
            return monitor_service.stop().to_dict()
        return snapshot.to_dict()

    @application.post("/api/monitoramentos", status_code=status.HTTP_202_ACCEPTED)
    def create_monitoring(payload: MonitoringRequest) -> dict[str, Any]:
        return _create_monitoring(payload)

    @application.get("/api/monitoramentos")
    def list_monitorings() -> dict[str, Any]:
        return {"items": [item.to_dict() for item in snapshots()]}

    @application.get("/api/monitoramentos/{monitoring_id}")
    def get_monitoring(monitoring_id: int) -> dict[str, Any]:
        return _manager_snapshot(manager_service, monitoring_id).to_dict()

    @application.post("/api/monitoramentos/{monitoring_id}/pausar")
    def pause_monitoring(monitoring_id: int) -> dict[str, Any]:
        active_manager = _require_manager(manager_service)
        try:
            return active_manager.pause(monitoring_id).to_dict()
        except MonitoringNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @application.post("/api/monitoramentos/{monitoring_id}/retomar")
    def resume_monitoring(monitoring_id: int) -> dict[str, Any]:
        active_manager = _require_manager(manager_service)
        if storage is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        persisted = storage.get_monitor(monitoring_id)
        if persisted is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Monitoramento {monitoring_id} não encontrado.",
            )
        try:
            return active_manager.resume(
                monitoring_id,
                _settings_from_monitor(persisted),
            ).to_dict()
        except ConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except MonitorAlreadyRunning as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @application.delete("/api/monitoramentos/{monitoring_id}")
    def remove_monitoring(monitoring_id: int) -> dict[str, Any]:
        active_manager = _require_manager(manager_service)
        try:
            active_manager.remove(monitoring_id)
        except MonitoringNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"removed": True, "monitoring_id": monitoring_id}

    @application.get("/api/monitoramentos/{monitoring_id}/historico")
    def monitoring_history_by_id(
        monitoring_id: int,
        limit: int = Query(default=50, ge=1, le=1_000),
    ) -> dict[str, Any]:
        active_manager = _require_manager(manager_service)
        try:
            items = active_manager.history(monitoring_id, limit)
        except MonitoringNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"monitoring_id": monitoring_id, "items": items}

    return application


def _require_manager(manager: MonitoringManager | None) -> MonitoringManager:
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gerenciador de múltiplos monitoramentos indisponível.",
        )
    return manager


def _manager_snapshot(manager: MonitoringManager | None, monitoring_id: int) -> MonitorSnapshot:
    active_manager = _require_manager(manager)
    try:
        return active_manager.snapshot(monitoring_id)
    except MonitoringNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _station_labels(
    catalog_provider: Callable[[], dict[str, Any]],
    origin: str,
    destination: str,
) -> tuple[str, str]:
    try:
        stations = catalog_provider().get("stations", [])
        names = {str(station["id"]): str(station["name"]) for station in stations}
    except (httpx.HTTPError, PortalError, ConfigurationError, KeyError, TypeError, ValueError):
        LOGGER.warning("Não foi possível resolver os nomes das estações para o alerta.")
        return origin, destination
    return names.get(origin, origin), names.get(destination, destination)


app = create_app()


def main() -> None:
    log_level = os.getenv("EFVM_LOG_LEVEL", "INFO").strip().upper()
    _configure_logging(log_level)
    port = int(os.getenv("EFVM_WEB_PORT", "8000"))
    LOGGER.info("Interface local disponível em http://127.0.0.1:%s", port)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level=log_level.casefold())


if __name__ == "__main__":
    main()
