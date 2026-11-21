"""Prova offline da troca de fonte pelo mesmo contrato usado pelos workers."""

import json
import time
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from functools import partial
from pathlib import Path

import pytest

from efvm_monitor.availability import AvailabilityObservation, adapt_availability
from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import LOCAL_TIMEZONE, Settings
from efvm_monitor.database import MonitoringRepository
from efvm_monitor.manager import MonitoringManager, MonitoringNotFound

NOW = datetime(2026, 11, 21, 10, tzinfo=UTC)
FIXTURES = Path(__file__).parent / "fixtures" / "availability_sources.json"


class FixtureClient(AbstractContextManager):
    def __init__(self, settings, *, payload):
        self.settings = settings
        self.payload = payload

    def __exit__(self, *_):
        pass

    def check(self) -> AvailabilityResult:
        try:
            observation = self.read_offer()
        except PermissionError:
            return AvailabilityResult(AvailabilityStatus.ERRO, "Autenticação inválida.")
        except ConnectionError:
            return AvailabilityResult(AvailabilityStatus.ERRO, "Fonte indisponível.")
        except (KeyError, TypeError, ValueError):
            return AvailabilityResult(AvailabilityStatus.ERRO, "Oferta ausente ou inválida.")

        # Horário e tarifa conhecidos da consulta fictícia, independentes da resposta.
        departure = datetime.combine(self.settings.travel_date, clock_time(7), LOCAL_TIMEZONE)
        return adapt_availability(
            self.settings,
            observation,
            expected_source=self.source_id,
            expected_departure=departure,
            now=NOW,
            max_age=timedelta(minutes=5),
            fare_code="INT",
        )


class OfferList(FixtureClient):
    source_id = "list"

    def read_offer(self) -> AvailabilityObservation:
        if self.payload["status_code"] == 401:
            raise PermissionError
        if self.payload["status_code"] != 200:
            raise ConnectionError
        # A lista simulada descreve uma viagem; a classe precisa ter uma única oferta.
        (row,) = [r for r in self.payload["offers"] if r["cabin"] == self.settings.travel_class]
        return AvailabilityObservation(
            source_id=self.source_id,
            railway_code=row["railway"],
            origin=row["from"],
            destination=row["to"],
            departure_at=datetime.fromisoformat(row["departure"]),
            travel_class=row["cabin"],
            observed_at=datetime.fromisoformat(row["checked"]) if row.get("checked") else None,
            fare_code=row["fare"],
            passengers=row["party"],
            eligible_seats=row["eligible"],
            sold_out={"sold_out": True, "open": False}[row["state"]],
        )


class ClassBoard(FixtureClient):
    source_id = "board"

    def read_offer(self) -> AvailabilityObservation:
        if self.payload["service"] == "unauthorized":
            raise PermissionError
        if self.payload["service"] != "ready":
            raise ConnectionError
        trip = self.payload["journey"]
        origin, destination = trip["stations"]
        cabin = self.settings.travel_class
        offer = trip["classes"][cabin]
        checked = offer.get("verified_at")
        return AvailabilityObservation(
            source_id=self.source_id,
            railway_code=trip["line"],
            origin=origin,
            destination=destination,
            departure_at=datetime.fromisoformat(trip["starts_at"]),
            travel_class=cabin,
            observed_at=datetime.fromisoformat(checked) if checked else None,
            fare_code=offer["tariff"],
            passengers=offer["passengers"],
            eligible_seats=offer["inventory"]["eligible_places"],
            sold_out=offer["inventory"]["exhausted"],
        )


@pytest.fixture
def payloads():
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture
def query(monkeypatch):
    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.astimezone().replace(tzinfo=None)

    monkeypatch.setattr("efvm_monitor.config.datetime", FixtureClock)
    monkeypatch.setattr("efvm_monitor.monitor.datetime", FixtureClock)
    return Settings.for_query(
        origin="A",
        destination="B",
        travel_date=date(2026, 12, 3),
        travel_class="EXE",
        base_url="https://example.invalid",
    )


@pytest.fixture(params=[OfferList, ClassBoard], ids=["lista", "quadro"])
def source(request, payloads):
    cls = request.param
    return partial(cls, payload=payloads[cls.source_id])


def test_classes_on_the_same_departure(source, query):
    for cabin, expected in [
        ("ECO", AvailabilityStatus.SEM_VAGA),
        ("EXE", AvailabilityStatus.TEM_VAGA),
    ]:
        result = source(replace(query, travel_class=cabin)).check()
        assert isinstance(result, AvailabilityResult)
        assert result.status is expected
        assert result.available_options == (1 if cabin == "EXE" else 0)


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "C"},
        {"destination": "C"},
        {"travel_date": date(2026, 12, 4)},
        {"travel_class": "OUTRA"},
        {"passengers": 2},
    ],
)
def test_mismatched_request(source, query, change):
    result = source(replace(query, **change)).check()
    assert result.status is AvailabilityStatus.ERRO
    assert result.available_options == 0


@pytest.mark.parametrize("scenario", ["fare", "stale", "undated", "offline", "unauthorized"])
def test_unsuitable_evidence_never_reports_availability(source, query, payloads, scenario):
    # Os quadros continuam atualizados e ambas as ofertas ainda contêm lugares elegíveis.
    listing, board = payloads["list"], payloads["board"]
    row = next(row for row in listing["offers"] if row["cabin"] == "EXE")
    offer = board["journey"]["classes"]["EXE"]
    if scenario == "fare":
        row["fare"] = offer["tariff"] = "REDUZIDA"
    elif scenario == "stale":
        row["checked"] = offer["verified_at"] = (NOW - timedelta(minutes=6)).isoformat()
    elif scenario == "undated":
        del row["checked"], offer["verified_at"]
    elif scenario == "offline":
        listing["status_code"], board["service"] = 503, "offline"
    else:
        listing["status_code"], board["service"] = 401, "unauthorized"

    result = source(query).check()
    assert result.status is AvailabilityStatus.ERRO
    assert result.available_options == 0


def test_switching_sources_keeps_individual_histories_and_alerts(tmp_path, query, payloads):
    storage = MonitoringRepository(tmp_path / "proof.db")
    storage.initialize()
    users = [
        storage.create_user(name=n, email=f"{n}@example.test", password_hash="unused")
        for n in ("ana", "bia")
    ]
    monitors = [storage.create_monitor(query, user_id=u.id) for u in users]
    previous = [AvailabilityStatus.SEM_VAGA, AvailabilityStatus.TEM_VAGA]
    previous_check = (NOW - timedelta(hours=1)).astimezone().isoformat(timespec="seconds")
    for monitor, status in zip(monitors, previous, strict=True):
        storage.record_check(monitor.id, status, "Anterior", previous_check)

    alerts = []
    for cls in (OfferList, ClassBoard):
        manager = MonitoringManager(
            storage,
            client_factory=partial(cls, payload=payloads[cls.source_id]),
            notifier=lambda _, result, monitor_id, __: alerts.append((monitor_id, result.status)),
        )
        try:
            for monitor, user in zip(monitors, users, strict=True):
                manager.resume(monitor.id, query, user_id=user.id)
            deadline = time.monotonic() + 5
            while any(
                manager.snapshot(m.id, user_id=u.id).status != "TEM_VAGA"
                for m, u in zip(monitors, users, strict=True)
            ):
                assert time.monotonic() < deadline, "O worker não concluiu a consulta simulada."
                time.sleep(0.01)
        finally:
            manager.shutdown()

    for monitor, user, prior in zip(monitors, users, previous, strict=True):
        history = manager.history(monitor.id, user_id=user.id)
        assert [row["result"] for row in history] == ["TEM_VAGA", "TEM_VAGA", prior.value]
        assert all(row["monitoring_id"] == monitor.id for row in history)
    with pytest.raises(MonitoringNotFound):
        manager.history(monitors[0].id, user_id=users[1].id)
    assert alerts == [(monitors[0].id, AvailabilityStatus.TEM_VAGA)]
