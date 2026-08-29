from __future__ import annotations

import json
import logging

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


def test_json_log_formatter_keeps_only_known_operational_fields() -> None:
    record = logging.LogRecord(
        name="efvm_monitor.monitor",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Consulta concluída.",
        args=(),
        exc_info=None,
    )
    record.event = "availability_check_completed"
    record.monitoring_id = 12
    record.user_id = 3
    record.duration_ms = 24.5
    record.password = "não-deve-aparecer"

    payload = json.loads(cli.JsonLogFormatter().format(record))

    assert payload["event"] == "availability_check_completed"
    assert payload["monitoring_id"] == 12
    assert payload["user_id"] == 3
    assert payload["duration_ms"] == 24.5
    assert "password" not in payload
