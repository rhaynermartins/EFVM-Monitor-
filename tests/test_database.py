from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from efvm_monitor.checker import AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, MonitorStatus


def settings() -> Settings:
    return Settings.for_query(
        origin="7185",
        destination="7157",
        travel_date=date.today() + timedelta(days=10),
        travel_class="Executiva",
        passengers=1,
        check_interval_seconds=300,
    )


def repository(tmp_path: Path) -> MonitoringRepository:
    instance = MonitoringRepository(tmp_path / "data" / "monitor.db")
    instance.initialize()
    return instance


def test_initialization_is_idempotent_and_non_destructive(tmp_path: Path) -> None:
    first = repository(tmp_path)
    monitor = first.create_monitor(settings())

    restarted = MonitoringRepository(first.database_path)
    restarted.initialize()

    recovered = restarted.get_monitor(monitor.id)
    assert recovered is not None
    assert recovered.origin == "7185"
    assert recovered.travel_class == "Executiva"
    assert recovered.active is True

    assert len(restarted.list_monitors()) == 1


def test_records_history_and_availability_changes(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    monitor = storage.create_monitor(settings())

    storage.record_check(
        monitor.id,
        AvailabilityStatus.TEM_VAGA,
        "Disponível.",
        "2026-08-28T10:00:00-03:00",
    )
    storage.record_check(
        monitor.id,
        AvailabilityStatus.TEM_VAGA,
        "Continua disponível.",
        "2026-08-28T10:05:00-03:00",
    )
    storage.record_check(
        monitor.id,
        AvailabilityStatus.SEM_VAGA,
        "Indisponível.",
        "2026-08-28T10:10:00-03:00",
    )
    storage.record_check(
        monitor.id,
        AvailabilityStatus.ERRO,
        "Portal temporariamente indisponível.",
        "2026-08-28T10:15:00-03:00",
    )

    recovered = storage.get_monitor(monitor.id)
    history = storage.history(monitor.id)

    assert recovered is not None
    assert recovered.last_result == "ERRO"
    assert recovered.last_checked_at == "2026-08-28T10:15:00-03:00"
    assert recovered.availability_changed_at == "2026-08-28T10:10:00-03:00"
    assert [entry.result for entry in history] == [
        "ERRO",
        "SEM_VAGA",
        "TEM_VAGA",
        "TEM_VAGA",
    ]


def test_pauses_and_recovers_active_monitor(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    active = storage.create_monitor(settings())

    assert storage.latest_active_monitor() == active

    storage.set_status(active.id, MonitorStatus.PAUSED)

    paused = storage.get_monitor(active.id)
    assert paused is not None
    assert paused.status is MonitorStatus.PAUSED
    assert storage.latest_active_monitor() is None
