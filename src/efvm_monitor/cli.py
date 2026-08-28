"""Interface de linha de comando do monitor EFVM."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus, EFVMClient
from efvm_monitor.config import ConfigurationError, Settings
from efvm_monitor.notifier import (
    NotificationSendError,
    NotificationService,
    WhatsAppConfigurationError,
)

LOGGER = logging.getLogger(__name__)
EXIT_CODES = {
    AvailabilityStatus.TEM_VAGA: 0,
    AvailabilityStatus.SEM_VAGA: 1,
    AvailabilityStatus.ERRO: 2,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Consulta disponibilidade da EFVM sem reservar ou comprar passagens."
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="repete a consulta no intervalo configurado; por padrão consulta uma vez",
    )
    parser.add_argument(
        "--env-file",
        help="caminho de um arquivo .env alternativo",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("monitor", "test-whatsapp"),
        default="monitor",
        help="use test-whatsapp para validar a Cloud API sem consultar passagens",
    )
    return parser


def _configure_logging(level: str) -> None:
    log_directory = Path("logs")
    log_directory.mkdir(exist_ok=True)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_directory / "efvm-monitor.log",
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    logging.basicConfig(
        level=getattr(logging, level),
        handlers=[console, file_handler],
        force=True,
    )


def _print_result(result: AvailabilityResult) -> None:
    print(f"{result.status.value} | {result.message}", flush=True)


def _notify(
    notifier: NotificationService,
    settings: Settings,
    result: AvailabilityResult,
) -> None:
    notifier.notify(
        settings,
        result,
        monitoring_id=None,
        detected_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def run_once(settings: Settings, notifier: NotificationService | None = None) -> int:
    with EFVMClient(settings) as client:
        result = client.check()
    _print_result(result)
    _notify(notifier or NotificationService(), settings, result)
    return EXIT_CODES[result.status]


def watch(settings: Settings, notifier: NotificationService | None = None) -> int:
    notification_service = notifier or NotificationService()
    previous_status: AvailabilityStatus | None = None
    with EFVMClient(settings) as client:
        while True:
            result = client.check()
            _print_result(result)
            became_available = (
                result.status is AvailabilityStatus.TEM_VAGA
                and previous_status is not result.status
            )
            if became_available:
                _notify(notification_service, settings, result)
            previous_status = result.status
            time.sleep(settings.check_interval_seconds)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "test-whatsapp":
        _configure_logging(os.getenv("EFVM_LOG_LEVEL", "INFO").strip().upper())
        try:
            result = NotificationService().send_test()
        except (NotificationSendError, WhatsAppConfigurationError) as exc:
            print(f"ERRO | {exc}", file=sys.stderr)
            return EXIT_CODES[AvailabilityStatus.ERRO]
        print(
            f"WHATSAPP_ENVIADO | mensagem de teste enviada em {result.attempts} tentativa(s)."
        )
        return 0

    try:
        settings = Settings.from_env(args.env_file)
    except ConfigurationError as exc:
        print(f"ERRO | {exc}", file=sys.stderr)
        return EXIT_CODES[AvailabilityStatus.ERRO]

    _configure_logging(settings.log_level)
    LOGGER.info(
        "Monitor configurado para %s -> %s em %s.",
        settings.origin,
        settings.destination,
        settings.travel_date.isoformat(),
    )

    try:
        return watch(settings) if args.watch else run_once(settings)
    except KeyboardInterrupt:
        LOGGER.info("Monitor encerrado pelo usuário.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
