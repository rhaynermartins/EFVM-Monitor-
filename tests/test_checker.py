from __future__ import annotations

import pytest

from efvm_monitor.checker import AvailabilityStatus, EFVMClient, PortalError


def test_reports_available_when_portal_returns_options() -> None:
    result = EFVMClient._parse_availability(
        {"excessao": None, "passagensIda": [{"descricaoClasse": "Econômica"}]}
    )

    assert result.status is AvailabilityStatus.TEM_VAGA
    assert result.available_options == 1


def test_reports_unavailable_only_for_explicit_portal_message() -> None:
    result = EFVMClient._parse_availability(
        {
            "excessao": {
                "tipo": "N",
                "descricao": "Não há passagens para a pesquisa informada.",
            },
            "passagensIda": None,
        }
    )

    assert result.status is AvailabilityStatus.SEM_VAGA


def test_rejects_ambiguous_empty_response() -> None:
    with pytest.raises(PortalError, match="sem opções"):
        EFVMClient._parse_availability({"excessao": None, "passagensIda": None})


def test_resolves_station_name_ignoring_accents_and_case() -> None:
    station = EFVMClient._find_station(
        [
            {
                "id": 7157,
                "codigo": "PNEP",
                "descricaoInternet": "Pedro Nolasco (Cariacica / Vitória)",
                "descricaoDetalhada": "PEDRO NOLASCO",
            }
        ],
        "pedro nolasco (cariacica / vitoria)",
        "destino",
    )

    assert station["id"] == 7157


def test_does_not_guess_partial_station_name() -> None:
    with pytest.raises(PortalError, match="resolver origem"):
        EFVMClient._find_station(
            [{"id": 7185, "codigo": "BHEP", "descricaoInternet": "Belo Horizonte"}],
            "Belo",
            "origem",
        )
