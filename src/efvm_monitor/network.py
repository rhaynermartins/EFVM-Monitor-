"""Configuração HTTPS compartilhada pelos clientes externos."""

from __future__ import annotations

import ssl

import truststore


def verified_ssl_context() -> ssl.SSLContext:
    """Usa certificados confiáveis do sistema operacional sem desativar TLS."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
