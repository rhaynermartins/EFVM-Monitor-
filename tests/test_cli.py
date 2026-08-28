from __future__ import annotations

from efvm_monitor import cli
from efvm_monitor.notifier import SendResult, WhatsAppConfigurationError


class SuccessfulNotificationService:
    def send_test(self) -> SendResult:
        return SendResult(external_message_id="wamid.test", attempts=1)


class UnconfiguredNotificationService:
    def send_test(self) -> SendResult:
        raise WhatsAppConfigurationError("Configuração incompleta.")


def test_whatsapp_command_sends_message_without_loading_trip(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(cli, "NotificationService", SuccessfulNotificationService)
    monkeypatch.setattr(cli, "_configure_logging", lambda _level: None)

    exit_code = cli.main(["test-whatsapp"])

    assert exit_code == 0
    assert "WHATSAPP_ENVIADO" in capsys.readouterr().out


def test_whatsapp_command_reports_missing_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "NotificationService", UnconfiguredNotificationService)
    monkeypatch.setattr(cli, "_configure_logging", lambda _level: None)

    exit_code = cli.main(["test-whatsapp"])

    assert exit_code == 2
    assert "Configuração incompleta" in capsys.readouterr().err
