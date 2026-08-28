"""Primitivas de autenticação sem armazenar senhas ou sessões em texto puro."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
SCRYPT_NAME = "scrypt"
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LENGTH = 32


class AuthenticationError(ValueError):
    """Indica credenciais inválidas sem revelar qual campo não conferiu."""


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    user_id: int
    name: str
    email: str
    csrf_token: str
    expires_at: str

    def to_user_dict(self) -> dict[str, str | int]:
        return {"id": self.user_id, "name": self.name, "email": self.email}


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    token: str
    token_hash: str
    csrf_token: str
    expires_at: str


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise AuthenticationError("Informe um e-mail válido.")
    return email


def hash_password(password: str) -> str:
    _validate_password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_LENGTH,
    )
    return "$".join(
        (
            SCRYPT_NAME,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            _encode(salt),
            _encode(derived),
        )
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, n, r, p, salt, expected = encoded.split("$", 5)
        if algorithm != SCRYPT_NAME:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_decode(expected)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, _decode(expected))


def new_session_credentials(duration_days: int = 30) -> SessionCredentials:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = (
        datetime.now().astimezone() + timedelta(days=duration_days)
    ).isoformat(timespec="seconds")
    return SessionCredentials(
        token=token,
        token_hash=token_digest(token),
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secure_token_matches(received: str | None, expected: str) -> bool:
    return bool(received and hmac.compare_digest(received, expected))


def _validate_password(password: str) -> None:
    if len(password) < 10:
        raise AuthenticationError("A senha deve ter pelo menos 10 caracteres.")
    if len(password) > 128:
        raise AuthenticationError("A senha deve ter no máximo 128 caracteres.")


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)
