from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from efvm_monitor.auth import hash_password, verify_password
from efvm_monitor.database import MonitoringRepository
from efvm_monitor.manager import MonitoringManager
from efvm_monitor.web import create_app

CATALOG = {"stations": [], "classes": [], "sale_window_days": 45}
ACCOUNT = {
    "name": "Rhayner Martins",
    "email": "rhayner@example.com",
    "password": "senha-segura-123",
}


@pytest.fixture
def authenticated_app(tmp_path: Path) -> Iterator[tuple[TestClient, MonitoringRepository]]:
    storage = MonitoringRepository(tmp_path / "auth.db")
    manager = MonitoringManager(storage, notifier=lambda *_: None)
    application = create_app(
        manager=manager,
        repository=storage,
        catalog_provider=lambda: CATALOG,
    )
    with TestClient(application) as client:
        yield client, storage


def register(client: TestClient, account: dict[str, str] = ACCOUNT) -> dict[str, Any]:
    response = client.post("/api/auth/cadastro", json=account)
    assert response.status_code == 201
    return response.json()


def test_hashes_password_with_unique_scrypt_salts() -> None:
    first = hash_password(ACCOUNT["password"])
    second = hash_password(ACCOUNT["password"])

    assert first.startswith("scrypt$")
    assert first != second
    assert verify_password(ACCOUNT["password"], first) is True
    assert verify_password("senha-incorreta", first) is False


def test_requires_account_before_opening_dashboard(
    authenticated_app: tuple[TestClient, MonitoringRepository],
) -> None:
    client, _ = authenticated_app

    page = client.get("/", follow_redirects=False)
    api = client.get("/api/monitoramentos")

    assert page.status_code == 303
    assert page.headers["location"] == "/login"
    assert api.status_code == 401


def test_registers_with_http_only_cookie_and_persists_session(
    authenticated_app: tuple[TestClient, MonitoringRepository],
) -> None:
    client, storage = authenticated_app

    response = client.post("/api/auth/cadastro", json=ACCOUNT)
    body = response.json()
    current = client.get("/api/auth/me")
    saved_user = storage.get_user_by_email(ACCOUNT["email"])

    assert response.status_code == 201
    assert body["user"]["email"] == ACCOUNT["email"]
    assert body["csrf_token"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert current.status_code == 200
    assert current.json()["user"] == body["user"]
    assert saved_user is not None
    assert saved_user.password_hash != ACCOUNT["password"]
    assert verify_password(ACCOUNT["password"], saved_user.password_hash) is True


def test_rejects_state_changes_without_valid_csrf(
    authenticated_app: tuple[TestClient, MonitoringRepository],
) -> None:
    client, _ = authenticated_app
    session = register(client)

    missing = client.post("/api/auth/logout")
    invalid = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": "token-invalido"},
    )
    valid = client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )

    assert missing.status_code == 403
    assert invalid.status_code == 403
    assert valid.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


def test_logs_in_again_with_persisted_credentials(
    authenticated_app: tuple[TestClient, MonitoringRepository],
) -> None:
    client, _ = authenticated_app
    session = register(client)
    client.post(
        "/api/auth/logout",
        headers={"X-CSRF-Token": session["csrf_token"]},
    )

    rejected = client.post(
        "/api/auth/login",
        json={"email": ACCOUNT["email"], "password": "senha-incorreta"},
    )
    accepted = client.post(
        "/api/auth/login",
        json={"email": ACCOUNT["email"], "password": ACCOUNT["password"]},
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert client.get("/").status_code == 200


def test_session_survives_application_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "persistent-session.db"
    first_storage = MonitoringRepository(database_path)
    first_manager = MonitoringManager(first_storage, notifier=lambda *_: None)
    first_app = create_app(
        manager=first_manager,
        repository=first_storage,
        catalog_provider=lambda: CATALOG,
    )
    with TestClient(first_app) as first_client:
        register(first_client)
        session_cookie = first_client.cookies.get("efvm_session")
        assert session_cookie

    restarted_storage = MonitoringRepository(database_path)
    restarted_manager = MonitoringManager(restarted_storage, notifier=lambda *_: None)
    restarted_app = create_app(
        manager=restarted_manager,
        repository=restarted_storage,
        catalog_provider=lambda: CATALOG,
    )
    with TestClient(restarted_app) as restarted_client:
        restarted_client.cookies.set("efvm_session", session_cookie)
        current = restarted_client.get("/api/auth/me")

    assert current.status_code == 200
    assert current.json()["user"]["email"] == ACCOUNT["email"]
