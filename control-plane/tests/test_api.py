import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient

from squash_control_plane.config import get_settings
from squash_control_plane.main import create_app
from squash_control_plane.models import Device


def configure_test_environment(monkeypatch) -> None:
    monkeypatch.setenv("SQUASH_API_KEY_SHA256", hashlib.sha256(b"test-key").hexdigest())
    monkeypatch.setenv("SQUASH_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    get_settings.cache_clear()


def test_health_is_public(monkeypatch) -> None:
    configure_test_environment(monkeypatch)

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "squash-control-plane"}


def test_auth_check_rejects_missing_key(monkeypatch) -> None:
    configure_test_environment(monkeypatch)

    with TestClient(create_app()) as client:
        response = client.get("/v1/auth/check")

    assert response.status_code == 401


def test_auth_check_accepts_valid_key(monkeypatch) -> None:
    configure_test_environment(monkeypatch)

    with TestClient(create_app()) as client:
        response = client.get("/v1/auth/check", headers={"X-API-Key": "test-key"})

    assert response.status_code == 200
    assert response.json() == {"status": "authenticated"}


def test_execution_dispatch_is_idempotent(monkeypatch) -> None:
    configure_test_environment(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        with app.state.database.session_factory.begin() as session:
            device = Device(fingerprint="a" * 64, hostname="TEST-PC")
            session.add(device)
            session.flush()
            device_id = device.id

        headers = {"X-API-Key": "test-key", "Idempotency-Key": "test-request-1"}
        body = {"script": "Write-Output 'hello'", "timeout_seconds": 30}
        first = client.post(f"/v1/devices/{device_id}/executions", headers=headers, json=body)
        duplicate = client.post(f"/v1/devices/{device_id}/executions", headers=headers, json=body)

    assert first.status_code == 202, first.text
    assert duplicate.status_code == 202
    assert duplicate.json()["execution_id"] == first.json()["execution_id"]


def test_idempotency_key_conflict_is_rejected(monkeypatch) -> None:
    configure_test_environment(monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        with app.state.database.session_factory.begin() as session:
            device = Device(fingerprint="b" * 64, hostname="TEST-PC")
            session.add(device)
            session.flush()
            device_id = device.id

        headers = {"X-API-Key": "test-key", "Idempotency-Key": "same-key"}
        first = client.post(
            f"/v1/devices/{device_id}/executions",
            headers=headers,
            json={"script": "Get-Date"},
        )
        conflict = client.post(
            f"/v1/devices/{device_id}/executions",
            headers=headers,
            json={"script": "Get-Process"},
        )

    assert first.status_code == 202
    assert conflict.status_code == 409


def test_unknown_device_is_rejected(monkeypatch) -> None:
    configure_test_environment(monkeypatch)

    with TestClient(create_app()) as client:
        response = client.post(
            f"/v1/devices/{uuid4()}/executions",
            headers={"X-API-Key": "test-key", "Idempotency-Key": "missing-device"},
            json={"script": "Get-Date"},
        )

    assert response.status_code == 404


def test_api_client_can_be_issued_and_revoked(monkeypatch) -> None:
    configure_test_environment(monkeypatch)
    with TestClient(create_app()) as client:
        issued = client.post(
            "/v1/api-clients",
            headers={"X-API-Key": "test-key"},
            json={"name": "AI driver"},
        )
        assert issued.status_code == 201, issued.text
        issued_body = issued.json()

        authenticated = client.get(
            "/v1/devices", headers={"X-API-Key": issued_body["api_key"]}
        )
        revoked = client.post(
            f"/v1/api-clients/{issued_body['id']}/revoke",
            headers={"X-API-Key": "test-key"},
        )
        rejected = client.get(
            "/v1/devices", headers={"X-API-Key": issued_body["api_key"]}
        )

    assert authenticated.status_code == 200
    assert revoked.status_code == 200
    assert rejected.status_code == 401
