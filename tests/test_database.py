from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from efvm_monitor.auth import hash_password
from efvm_monitor.checker import AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.database import MonitoringRepository, MonitorStatus, NotificationStatus


def settings() -> Settings:
    return Settings.for_query(
        origin="7185",
        destination="7157",
        travel_date=date.today() + timedelta(days=10),
        travel_class="Executiva",
        passengers=1,
        check_interval_seconds=300,
        whatsapp_enabled=True,
        origin_label="Belo Horizonte",
        destination_label="Pedro Nolasco",
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
    assert recovered.whatsapp_enabled is True
    assert recovered.origin_label == "Belo Horizonte"

    assert len(restarted.list_monitors()) == 1


def test_reports_database_availability_after_initialization(tmp_path: Path) -> None:
    unavailable = MonitoringRepository(tmp_path / "missing" / "monitor.db")
    available = repository(tmp_path)

    assert unavailable.is_available() is False
    assert available.is_available() is True


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


def test_records_and_deduplicates_whatsapp_delivery(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    monitor = storage.create_monitor(settings())
    detected_at = "2026-08-28T11:00:00-03:00"

    delivery = storage.begin_notification(
        monitor.id,
        detected_at=detected_at,
        result=AvailabilityStatus.TEM_VAGA,
        channel="WHATSAPP",
        message="Passagem encontrada.",
    )
    duplicate = storage.begin_notification(
        monitor.id,
        detected_at=detected_at,
        result=AvailabilityStatus.TEM_VAGA,
        channel="WHATSAPP",
        message="Passagem encontrada novamente.",
    )

    assert delivery is not None
    assert duplicate is None

    storage.complete_notification(
        delivery.id,
        status=NotificationStatus.SENT,
        attempt_count=2,
        external_message_id="wamid.123",
    )

    history = storage.notification_history(monitor.id)
    assert len(history) == 1
    assert history[0].status is NotificationStatus.SENT
    assert history[0].attempt_count == 2
    assert history[0].external_message_id == "wamid.123"


def test_filters_monitors_and_history_by_owner(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    first_user = storage.create_user(
        name="Usuário A",
        email="owner-a@example.com",
        password_hash=hash_password("senha-segura-123"),
    )
    second_user = storage.create_user(
        name="Usuário B",
        email="owner-b@example.com",
        password_hash=hash_password("senha-segura-456"),
    )
    first_monitor = storage.create_monitor(settings(), user_id=first_user.id)
    second_monitor = storage.create_monitor(settings(), user_id=second_user.id)
    storage.record_check(
        first_monitor.id,
        AvailabilityStatus.SEM_VAGA,
        "Indisponível.",
        "2026-08-28T12:00:00-03:00",
    )

    assert [item.id for item in storage.list_monitors(user_id=first_user.id)] == [
        first_monitor.id
    ]
    assert [item.id for item in storage.list_monitors(user_id=second_user.id)] == [
        second_monitor.id
    ]
    assert storage.get_monitor(first_monitor.id, user_id=second_user.id) is None
    assert storage.history(first_monitor.id, user_id=second_user.id) == []


def test_claims_legacy_data_only_for_explicit_email(tmp_path: Path) -> None:
    storage = repository(tmp_path)
    legacy_monitor = storage.create_monitor(settings())
    user = storage.create_user(
        name="Responsável legado",
        email="responsavel@example.com",
        password_hash=hash_password("senha-segura-123"),
    )

    rejected = storage.claim_legacy_data(
        user.id,
        user.email,
        "outra-pessoa@example.com",
    )
    accepted = storage.claim_legacy_data(
        user.id,
        user.email,
        "responsavel@example.com",
    )

    assert rejected is False
    assert accepted is True
    assert storage.get_monitor(legacy_monitor.id) is None
    assert storage.get_monitor(legacy_monitor.id, user_id=user.id) is not None
