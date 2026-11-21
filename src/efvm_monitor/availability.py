"""Adaptação em memória de evidência normalizada por um conector de confiança."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeGuard

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import LOCAL_TIMEZONE, Settings


@dataclass(frozen=True, slots=True)
class AvailabilityObservation:
    """Uma partida, com os mesmos identificadores canônicos usados na consulta.

    observed_at é a verificação desta linha na fonte. Um conector que só dispõe
    da atualização do quadro ou da hora do recebimento deve fornecer None.
    eligible_seats conta apenas lugares confirmados para a tarifa e classe.
    sold_out exige indicação explícita no contexto solicitado.
    """

    source_id: str
    observed_at: datetime | None
    railway_code: str
    origin: str
    destination: str
    departure_at: datetime
    travel_class: str
    fare_code: str
    passengers: int
    eligible_seats: int | None = None
    sold_out: bool = False
    failure_reason: str | None = None


def _aware(value: object) -> TypeGuard[datetime]:
    return isinstance(value, datetime) and value.utcoffset() is not None


def adapt_availability(
    settings: Settings,
    observation: AvailabilityObservation,
    *,
    expected_source: str,
    expected_departure: datetime,
    now: datetime,
    max_age: timedelta,
    fare_code: str = "INT",
) -> AvailabilityResult:
    """O chamador define a fonte confiável, a partida e a janela de validade.

    expected_source é definido pelo conector configurado, independentemente
    do conteúdo recebido. failure_reason deve ser uma mensagem sanitizada.
    """
    error = AvailabilityStatus.ERRO
    if not expected_source.strip() or observation.source_id != expected_source:
        return AvailabilityResult(error, "Procedência da observação não confirmada.")
    if observation.failure_reason is not None:
        return AvailabilityResult(error, observation.failure_reason or "Falha na fonte.")
    if not _aware(now) or not _aware(observation.observed_at):
        return AvailabilityResult(error, "Horário individual de verificação desconhecido.")
    age = now.astimezone(UTC) - observation.observed_at.astimezone(UTC)
    if not timedelta(0) <= age <= max_age:
        return AvailabilityResult(error, "Observação fora da janela de validade.")
    if not _aware(expected_departure) or not _aware(observation.departure_at):
        return AvailabilityResult(error, "Partida sem horário verificável.")
    if (
        expected_departure.astimezone(LOCAL_TIMEZONE).date() != settings.travel_date
        or observation.departure_at.astimezone(UTC) != expected_departure.astimezone(UTC)
        or observation.railway_code != settings.railway_code
        or observation.origin != settings.origin
        or observation.destination != settings.destination
        or observation.travel_class != settings.travel_class
        or not fare_code.strip()
        or observation.fare_code != fare_code
        or type(observation.passengers) is not int
        or observation.passengers < 1
        or observation.passengers != settings.passengers
    ):
        return AvailabilityResult(error, "A evidência não corresponde à viagem solicitada.")

    count = observation.eligible_seats
    if type(observation.sold_out) is not bool or (
        count is not None and (type(count) is not int or count < 0)
    ):
        return AvailabilityResult(error, "Evidência de disponibilidade inválida.")
    if observation.sold_out:
        if count:
            return AvailabilityResult(error, "Esgotamento e lugares elegíveis são contraditórios.")
        return AvailabilityResult(AvailabilityStatus.SEM_VAGA, "Esgotamento explícito na fonte.")
    if count is not None and count >= observation.passengers:
        return AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Opção elegível confirmada.", 1)
    return AvailabilityResult(error, "Evidência insuficiente para a quantidade solicitada.")
