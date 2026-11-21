from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from efvm_monitor.availability import AvailabilityObservation, adapt_availability
from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import LOCAL_TIMEZONE, Settings

NOW = datetime(2026, 11, 21, 4, 30, tzinfo=UTC)
DEPARTURE = datetime(2026, 12, 3, 7, tzinfo=LOCAL_TIMEZONE)
WINDOW = timedelta(minutes=5)
SOURCE = "fixture-local"


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr("efvm_monitor.config.travel_today", lambda: NOW.date())
    return Settings.for_query(
        origin="ORIGEM", destination="DESTINO", travel_date=DEPARTURE.date(), travel_class="ECO"
    )


@pytest.fixture
def row():
    return AvailabilityObservation(
        source_id=SOURCE,
        observed_at=NOW,
        railway_code="03",
        origin="ORIGEM",
        destination="DESTINO",
        departure_at=DEPARTURE,
        travel_class="ECO",
        fare_code="INT",
        passengers=1,
        eligible_seats=8,
    )


def read(settings, row):
    return adapt_availability(
        settings, row, expected_source=SOURCE, expected_departure=DEPARTURE, now=NOW, max_age=WINDOW
    )


def test_eligible_seats_return_one_trip_option(settings, row):
    result = read(settings, row)
    assert type(result) is AvailabilityResult
    assert result.status is AvailabilityStatus.TEM_VAGA
    assert result.available_options == 1


def test_two_classes_have_independent_outcomes(settings, row):
    sold = replace(row, eligible_seats=None, sold_out=True)
    executive = replace(row, travel_class="EXE")
    assert read(settings, sold).status is AvailabilityStatus.SEM_VAGA
    assert read(settings, executive).status is AvailabilityStatus.ERRO
    assert (
        read(replace(settings, travel_class="EXE"), executive).status is AvailabilityStatus.TEM_VAGA
    )


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "OUTRA"},
        {"destination": "OUTRA"},
        {"origin": "DESTINO", "destination": "ORIGEM"},
        {"departure_at": DEPARTURE + timedelta(hours=1)},
        {"departure_at": DEPARTURE + timedelta(days=1)},
        {"departure_at": DEPARTURE.replace(tzinfo=None)},
        {"railway_code": "OUTRA"},
        {"fare_code": "REDUZIDA"},
        {"passengers": 2},
        {"passengers": True},
        {"source_id": ""},
        {"source_id": "outra-fixture"},
        {"eligible_seats": None},
        {"eligible_seats": 0},
        {"eligible_seats": -1},
        {"eligible_seats": True},
        {"eligible_seats": 2.5},
        {"sold_out": True},
        {"sold_out": "sim"},
    ],
)
def test_mismatch_or_ambiguous_evidence_fails_closed(settings, row, change):
    result = read(settings, replace(row, **change))
    assert result.status is AvailabilityStatus.ERRO
    assert result.available_options == 0


@pytest.mark.parametrize(
    "row_time",
    [
        None,
        NOW.replace(tzinfo=None),
        NOW - WINDOW - timedelta(seconds=1),
        NOW + timedelta(seconds=1),
    ],
)
@pytest.mark.parametrize("sold_out", [False, True])
def test_new_board_timestamp_cannot_refresh_old_or_undated_trip(settings, row, row_time, sold_out):
    board = {
        "updated_at": NOW,
        "row": replace(
            row, observed_at=row_time, eligible_seats=0 if sold_out else 8, sold_out=sold_out
        ),
    }
    assert read(settings, board["row"]).status is AvailabilityStatus.ERRO


def test_boundary_and_timezone_offsets_use_the_instant(settings, row):
    other_zone = timezone(timedelta(hours=3))
    equivalent = replace(
        row,
        observed_at=(NOW - WINDOW).astimezone(other_zone),
        departure_at=DEPARTURE.astimezone(UTC),
    )
    assert read(settings, equivalent).status is AvailabilityStatus.TEM_VAGA


def test_query_date_must_match_departure_in_railway_timezone(settings, row):
    other_day = replace(settings, travel_date=DEPARTURE.date() + timedelta(days=1))
    assert read(other_day, row).status is AvailabilityStatus.ERRO


def test_insufficient_eligible_seats_for_requested_party(settings, row):
    party = replace(settings, passengers=2)
    assert (
        read(party, replace(row, passengers=2, eligible_seats=1)).status is AvailabilityStatus.ERRO
    )


def test_source_failure_has_priority_over_seat_count(settings, row):
    result = read(settings, replace(row, failure_reason="Autenticação expirada."))
    assert result == AvailabilityResult(AvailabilityStatus.ERRO, "Autenticação expirada.")
