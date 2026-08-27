"""Carregamento e validação das configurações do monitor."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """Indica que uma configuração obrigatória é inválida ou está ausente."""


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigurationError(f"Defina {name} no arquivo .env.")
    return value


def _integer(name: str, default: int, minimum: int, maximum: int | None = None) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} deve ser um número inteiro.") from exc

    if value < minimum or (maximum is not None and value > maximum):
        suffix = f" e no máximo {maximum}" if maximum is not None else ""
        raise ConfigurationError(f"{name} deve ser no mínimo {minimum}{suffix}.")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuração validada de uma consulta de disponibilidade."""

    origin: str
    destination: str
    travel_date: date
    travel_class: str
    passengers: int
    check_interval_seconds: int
    timeout_seconds: int
    log_level: str
    base_url: str
    railway_code: str
    alert_webhook_url: str | None

    @classmethod
    def from_env(cls, env_file: str | None = None) -> Settings:
        load_dotenv(dotenv_path=env_file)

        raw_date = _required("EFVM_TRAVEL_DATE")
        try:
            travel_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise ConfigurationError("EFVM_TRAVEL_DATE deve usar o formato AAAA-MM-DD.") from exc

        origin = _required("EFVM_ORIGIN")
        destination = _required("EFVM_DESTINATION")
        if origin.casefold() == destination.casefold():
            raise ConfigurationError("Origem e destino devem ser diferentes.")

        log_level = os.getenv("EFVM_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("EFVM_LOG_LEVEL possui um valor inválido.")

        base_url = os.getenv(
            "EFVM_BASE_URL", "https://tremdepassageiros.vale.com/sgpweb/rest"
        ).strip()
        if not base_url.startswith(("https://", "http://")):
            raise ConfigurationError("EFVM_BASE_URL deve começar com http:// ou https://.")

        webhook = os.getenv("ALERT_WEBHOOK_URL", "").strip() or None

        return cls(
            origin=origin,
            destination=destination,
            travel_date=travel_date,
            travel_class=os.getenv("EFVM_CLASS", "Econômica").strip() or "Econômica",
            passengers=_integer("EFVM_PASSENGERS", 1, 1, 10),
            check_interval_seconds=_integer("EFVM_CHECK_INTERVAL_SECONDS", 300, 60),
            timeout_seconds=_integer("EFVM_TIMEOUT_SECONDS", 30, 5, 120),
            log_level=log_level,
            base_url=base_url.rstrip("/"),
            railway_code=os.getenv("EFVM_RAILWAY_CODE", "03").strip() or "03",
            alert_webhook_url=webhook,
        )
