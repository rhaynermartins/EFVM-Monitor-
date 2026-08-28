from datetime import date, timedelta

import pytest

from efvm_monitor.checker import AvailabilityResult, AvailabilityStatus
from efvm_monitor.config import Settings
from efvm_monitor.notifier import (
    NotificationService,
    SMSConfig,
    SMSConfigurationError,
    TwilioSMSNotifier,
    format_sms_message,
    mask_phone,
    normalize_e164,
)


def settings(enabled: bool = True) -> Settings:
    return Settings.for_query(
        origin="7185", destination="7184", travel_date=date.today() + timedelta(days=5),
        travel_class="Econômica", sms_enabled=enabled,
        origin_label="Belo Horizonte", destination_label="Dois Irmãos",
    )


def config(**values) -> SMSConfig:
    defaults = dict(enabled=True, provider="twilio", account_sid="AC" + "1" * 32,
                    auth_token="secret", from_number="+15551234567",
                    recipient_phone="+5531999999999", dry_run=True)
    defaults.update(values)
    return SMSConfig(**defaults)


class SpySMS:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, _message: str):
        self.calls += 1
        raise AssertionError("A Twilio não deveria ser chamada.")


def test_sms_message_is_short_and_contains_trip() -> None:
    message = format_sms_message(settings())
    assert "Belo Horizonte -> Dois Irmaos" in message
    assert "Economica" in message
    assert "disponibilidade" in message.casefold()
    assert len(message) <= 320


def test_phone_normalization_and_masking() -> None:
    assert normalize_e164("+55 (31) 99999-9999") == "+5531999999999"
    assert mask_phone("+5531999999999").endswith("9999")
    with pytest.raises(SMSConfigurationError):
        normalize_e164("31999999999")


def test_dry_run_does_not_create_http_client() -> None:
    notifier = TwilioSMSNotifier(
        config(), client_factory=lambda: (_ for _ in ()).throw(AssertionError("HTTP chamado"))
    )
    result = notifier.send("Mensagem de teste")
    assert result.external_message_id == "DRY_RUN"
    assert result.attempts == 0


def test_disabled_sms_does_not_call_provider() -> None:
    spy = SpySMS()
    sms_config = config(enabled=False)
    service = NotificationService(sms_config=sms_config, sms_notifier=spy)
    service.notify(
        settings(), AvailabilityResult(AvailabilityStatus.TEM_VAGA, "Disponível", 1),
        None, "2026-08-28T10:00:00-03:00",
    )
    assert spy.calls == 0


def test_missing_credentials_are_reported() -> None:
    with pytest.raises(SMSConfigurationError, match="TWILIO_ACCOUNT_SID"):
        config(account_sid="", auth_token="", dry_run=False).validate()
