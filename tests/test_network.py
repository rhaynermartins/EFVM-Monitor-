from __future__ import annotations

import hashlib
import ssl
from importlib.resources import as_file, files

import pytest

from efvm_monitor import network


def test_verified_context_requires_certificate_and_hostname_validation() -> None:
    context = network.verified_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_vale_context_keeps_certificate_and_hostname_validation() -> None:
    context = network.vale_ssl_context()

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_bundled_vale_intermediate_has_expected_fingerprint() -> None:
    certificate = files("efvm_monitor").joinpath(network.VALE_INTERMEDIATE_CERTIFICATE)
    with as_file(certificate) as certificate_path:
        certificate_pem = certificate_path.read_text(encoding="ascii")

    certificate_der = ssl.PEM_cert_to_DER_cert(certificate_pem)

    assert hashlib.sha256(certificate_der).hexdigest() == network.VALE_INTERMEDIATE_SHA256


def test_vale_context_rejects_an_unexpected_intermediate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    certificate_path = tmp_path / network.VALE_INTERMEDIATE_CERTIFICATE
    certificate_path.parent.mkdir(parents=True)
    original = files("efvm_monitor").joinpath(network.VALE_INTERMEDIATE_CERTIFICATE).read_text(
        encoding="ascii"
    )
    certificate_path.write_text(original.replace("MIIEyD", "NIIEyD", 1), encoding="ascii")

    monkeypatch.setattr(network, "files", lambda _package: tmp_path)

    with pytest.raises(ssl.SSLError, match="certificado intermediário público"):
        network.vale_ssl_context()
