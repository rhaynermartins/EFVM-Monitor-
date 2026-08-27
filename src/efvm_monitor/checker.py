"""Cliente HTTP de leitura para consultar disponibilidade no portal da Vale."""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from efvm_monitor.config import Settings
from efvm_monitor.network import verified_ssl_context

LOGGER = logging.getLogger(__name__)
LOCAL_TIMEZONE = ZoneInfo("America/Sao_Paulo")


class AvailabilityStatus(StrEnum):
    TEM_VAGA = "TEM_VAGA"
    SEM_VAGA = "SEM_VAGA"
    ERRO = "ERRO"


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    status: AvailabilityStatus
    message: str
    available_options: int = 0


class PortalError(RuntimeError):
    """Indica resposta inesperada ou configuração incompatível com o portal."""


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    return " ".join(text.encode("ascii", "ignore").decode().casefold().split())


class EFVMClient:
    """Executa somente consultas públicas anteriores ao fluxo de compra."""

    RAILWAYS_PATH = "/externo/Ferrovia/publico/obterFerroviasInternet"
    STATIONS_PATH = "/externo/Ferrovia/publico/obterLocaisFerroviarios"
    CLASSES_PATH = "/externo/VendaInternet/publico/pesquisaClassePassagem"
    FARE_TYPES_PATH = "/externo/VendaInternet/publico/pesquisaTipoPassagem"
    AVAILABILITY_PATH = "/externo/VendaInternet/publico/pesquisaPassagem"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = httpx.Client(
            base_url=settings.base_url,
            timeout=settings.timeout_seconds,
            follow_redirects=False,
            verify=verified_ssl_context(),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "efvm-monitor/0.1 (availability-only proof of concept)",
            },
        )
        self._catalog: dict[str, Any] | None = None

    def __enter__(self) -> EFVMClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_public_catalog(self) -> dict[str, Any]:
        """Retorna somente os dados necessários para preencher o formulário local."""
        catalog = self._catalog or self._load_catalog()
        self._catalog = catalog
        stations = sorted(
            (
                {
                    "id": station["id"],
                    "name": station["descricaoInternet"],
                    "state": station.get("unidadeFederacao"),
                }
                for station in catalog["stations"]
            ),
            key=lambda station: _normalize(station["name"]),
        )
        allowed_classes = {"economica", "executiva"}
        classes = [
            {"id": travel_class["id"], "name": travel_class["nome"]}
            for travel_class in catalog["classes"]
            if _normalize(travel_class.get("nome", "")) in allowed_classes
        ]
        if not classes:
            raise PortalError("O portal não retornou classes Econômica ou Executiva.")
        return {
            "stations": stations,
            "classes": classes,
            "sale_window_days": catalog["sale_window_days"],
        }

    def check(self) -> AvailabilityResult:
        """Consulta uma vez e sempre devolve TEM_VAGA, SEM_VAGA ou ERRO."""
        try:
            catalog = self._catalog or self._load_catalog()
            self._catalog = catalog
            self._validate_date(catalog["sale_window_days"])

            origin = self._find_station(catalog["stations"], self.settings.origin, "origem")
            destination = self._find_station(
                catalog["stations"], self.settings.destination, "destino"
            )
            if origin["id"] == destination["id"]:
                raise PortalError("Origem e destino resolvidos para a mesma estação.")

            travel_class = self._find_class(catalog["classes"], self.settings.travel_class)
            normal_fare = next(
                (item for item in catalog["fare_types"] if item.get("codigo") == "INT"), None
            )
            if normal_fare is None:
                raise PortalError("O portal não retornou o tipo Tarifa Normal (INT).")

            response = self._post(
                self.AVAILABILITY_PATH,
                {
                    "codigoClasse": travel_class["id"],
                    "codigoFerrovia": self.settings.railway_code,
                    "codigoLocalOrigem": origin["id"],
                    "codigoLocalDestino": destination["id"],
                    "dataIda": self._travel_timestamp_ms(),
                    "detalheVenda": [
                        {
                            "detalhe": normal_fare["id"],
                            "funcionario": False,
                            "qtd": self.settings.passengers,
                        }
                    ],
                    "tremFerias": False,
                },
            )
            return self._parse_availability(response)
        except (httpx.HTTPError, PortalError, KeyError, TypeError, ValueError) as exc:
            LOGGER.error("Consulta não concluída: %s", exc)
            return AvailabilityResult(AvailabilityStatus.ERRO, str(exc))

    def _load_catalog(self) -> dict[str, Any]:
        railway_payload = self._post(self.RAILWAYS_PATH, {})
        railway = next(
            (
                item
                for item in railway_payload.get("ferrovias", [])
                if item.get("codigoFerroviaMalha") == self.settings.railway_code
            ),
            None,
        )
        if railway is None:
            raise PortalError(f"Ferrovia {self.settings.railway_code!r} não encontrada.")

        railway_request = {"codigoFerrovia": self.settings.railway_code}
        stations = self._post(
            self.STATIONS_PATH,
            {**railway_request, "tremFerias": False},
        ).get("locaisFerroviarios", [])
        classes = self._post(self.CLASSES_PATH, railway_request).get("classesPassagem", [])
        fare_types = self._post(self.FARE_TYPES_PATH, railway_request).get("tiposPassagem", [])

        if not stations or not classes or not fare_types:
            raise PortalError("O portal retornou um catálogo público incompleto.")

        try:
            sale_window_days = int(railway["quantidadeDiasLiberacaoVenda"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PortalError("O portal não informou a janela atual de venda.") from exc

        return {
            "stations": stations,
            "classes": classes,
            "fare_types": fare_types,
            "sale_window_days": sale_window_days,
        }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        LOGGER.debug("Consultando endpoint público %s", path)
        response = self._client.post(path, json=payload)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise PortalError(f"Resposta não JSON recebida de {path}.") from exc
        if not isinstance(data, dict):
            raise PortalError(f"Formato inesperado recebido de {path}.")
        return data

    def _validate_date(self, sale_window_days: int) -> None:
        today = datetime.now(LOCAL_TIMEZONE).date()
        days_ahead = (self.settings.travel_date - today).days
        if days_ahead < 1:
            raise PortalError("A data deve ser posterior ao dia atual.")
        if days_ahead > sale_window_days:
            raise PortalError(
                f"A data excede a janela atual de venda de {sale_window_days} dias."
            )

    def _travel_timestamp_ms(self) -> int:
        travel_datetime = datetime.combine(
            self.settings.travel_date,
            datetime.min.time().replace(hour=15),
            tzinfo=LOCAL_TIMEZONE,
        )
        return int(travel_datetime.timestamp() * 1000)

    @staticmethod
    def _find_station(
        stations: list[dict[str, Any]], value: str, field_name: str
    ) -> dict[str, Any]:
        normalized = _normalize(value)
        matches = [
            station
            for station in stations
            if normalized
            in {
                _normalize(station.get("id", "")),
                _normalize(station.get("codigo", "")),
                _normalize(station.get("descricaoInternet", "")),
                _normalize(station.get("descricaoDetalhada", "")),
            }
        ]
        if len(matches) != 1:
            raise PortalError(
                f"Não foi possível resolver {field_name} {value!r} de forma exata no portal."
            )
        return matches[0]

    @staticmethod
    def _find_class(classes: list[dict[str, Any]], value: str) -> dict[str, Any]:
        normalized = _normalize(value)
        matches = [
            travel_class
            for travel_class in classes
            if normalized
            in {
                _normalize(travel_class.get("id", "")),
                _normalize(travel_class.get("nome", "")),
            }
        ]
        if len(matches) != 1:
            raise PortalError(f"Classe {value!r} não encontrada de forma exata no portal.")
        return matches[0]

    @staticmethod
    def _parse_availability(data: dict[str, Any]) -> AvailabilityResult:
        options = data.get("passagensIda")
        if isinstance(options, list) and options:
            return AvailabilityResult(
                AvailabilityStatus.TEM_VAGA,
                f"O portal retornou {len(options)} opção(ões) disponível(is).",
                available_options=len(options),
            )

        exception = data.get("excessao")
        if isinstance(exception, dict):
            description = str(exception.get("descricao", "")).strip()
            if "nao ha passagens" in _normalize(description):
                return AvailabilityResult(
                    AvailabilityStatus.SEM_VAGA,
                    "O portal informou que não há passagens para a pesquisa.",
                )
            raise PortalError(description or "O portal retornou uma exceção sem descrição.")

        raise PortalError("Resposta sem opções e sem indicação explícita de indisponibilidade.")
