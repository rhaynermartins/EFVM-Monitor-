"""Configuração HTTPS compartilhada pelos clientes externos."""

from __future__ import annotations

import hashlib
import ssl
from importlib.resources import as_file, files

import truststore

VALE_INTERMEDIATE_CERTIFICATE = (
    "certificates/digicert-global-g2-tls-rsa-sha256-2020-ca1.pem"
)
VALE_INTERMEDIATE_SHA256 = (
    "c8025f9fc65fdfc95b3ca8cc7867b9a587b5277973957917463fc813d0b625a9"
)


def verified_ssl_context() -> ssl.SSLContext:
    """Usa certificados confiáveis do sistema operacional sem desativar TLS."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def vale_ssl_context() -> ssl.SSLContext:
    """Completa a cadeia pública incompleta da Vale mantendo verificação TLS integral."""
    context = verified_ssl_context()
    certificate = files("efvm_monitor").joinpath(VALE_INTERMEDIATE_CERTIFICATE)
    with as_file(certificate) as certificate_path:
        certificate_pem = certificate_path.read_text(encoding="ascii")
        certificate_der = ssl.PEM_cert_to_DER_cert(certificate_pem)
        fingerprint = hashlib.sha256(certificate_der).hexdigest()
        if fingerprint != VALE_INTERMEDIATE_SHA256:
            raise ssl.SSLError("O certificado intermediário público da Vale é inválido.")
        context.load_verify_locations(cafile=str(certificate_path))
    return context
