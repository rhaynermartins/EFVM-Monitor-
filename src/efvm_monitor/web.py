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
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from efvm_monitor.checker import EFVMClient, PortalError
from efvm_monitor.cli import _configure_logging
from efvm_monitor.config import ConfigurationError, Settings
from efvm_monitor.database import MonitoringRepository, PersistedMonitor
from efvm_monitor.monitor import MonitorAlreadyRunning, MonitorService
from efvm_monitor.notifier import NotificationService

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
    repository: MonitoringRepository | None = None,
    catalog_provider: Callable[[], dict[str, Any]] | None = None,
    notification_service: NotificationService | None = None,
) -> FastAPI:
    storage = repository
    notifications = notification_service or NotificationService(repository=storage)
    if monitor is None:
        storage = storage or MonitoringRepository(
            os.getenv("EFVM_DATABASE_PATH", "data/efvm-monitor.db")
        )
        if notification_service is None:
            notifications = NotificationService(repository=storage)
        monitor_service = MonitorService(
            repository=storage,
            notifier=notifications.notify,
        )
    else:
        monitor_service = monitor
    provide_catalog = catalog_provider or CatalogCache().get

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if storage is not None:
            storage.initialize()
            saved_monitor = storage.latest_monitor()
            if saved_monitor is not None:
                saved_settings: Settings | None = None
                if saved_monitor.active:
                    try:
                        saved_settings = _settings_from_monitor(saved_monitor)
                    except ConfigurationError as exc:
                        LOGGER.error("Monitor salvo não pôde ser retomado: %s", exc)
                monitor_service.restore(saved_monitor, saved_settings)
        yield
        monitor_service.shutdown()

    application = FastAPI(
        title="EFVM Monitor",
        description="Interface local de consulta de disponibilidade, sem compra de passagem.",
        version="0.4.0",
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
            },
        )

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
            )
            return monitor_service.start(settings).to_dict()
        except ConfigurationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except MonitorAlreadyRunning as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @application.get("/api/monitoramento")
    def monitoring_status() -> dict[str, Any]:
        return monitor_service.snapshot().to_dict()

    @application.get("/api/monitoramento/historico")
    def monitoring_history(
        limit: int = Query(default=50, ge=1, le=1_000),
    ) -> dict[str, Any]:
        snapshot = monitor_service.snapshot()
        return {
            "monitoring_id": snapshot.monitoring_id,
            "items": monitor_service.history(limit),
        }

    @application.delete("/api/monitoramento")
    def stop_monitoring() -> dict[str, Any]:
        return monitor_service.stop().to_dict()

    return application


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
