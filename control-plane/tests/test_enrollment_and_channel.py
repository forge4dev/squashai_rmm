import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient

from squash_control_plane.config import get_settings
from squash_control_plane.executions import expire_jobs
from squash_control_plane.main import create_app
from squash_control_plane.models import Execution

API_HEADERS = {"X-API-Key": "test-key"}
FINGERPRINT = "c" * 64


def configure(monkeypatch) -> None:
    monkeypatch.setenv("SQUASH_API_KEY_SHA256", hashlib.sha256(b"test-key").hexdigest())
    monkeypatch.setenv("SQUASH_DATABASE_URL", "sqlite+pysqlite:///:memory:")
    monkeypatch.setenv("SQUASH_HEARTBEAT_GRACE_SECONDS", "5")
    get_settings.cache_clear()


def enroll(client: TestClient, fingerprint: str = FINGERPRINT) -> dict:
    issued = client.post(
        "/v1/enrollment-tokens",
        headers=API_HEADERS,
        json={"expected_fingerprint": fingerprint, "ttl_seconds": 60},
    )
    assert issued.status_code == 201, issued.text
    response = client.post(
        "/v1/agent/enroll",
        json={
            "token": issued.json()["token"],
            "fingerprint": fingerprint,
            "hostname": "TEST-PC",
            "agent_version": "0.1.0",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_enrollment_token_is_device_bound_and_single_use(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(create_app()) as client:
        issued = client.post(
            "/v1/enrollment-tokens",
            headers=API_HEADERS,
            json={"expected_fingerprint": FINGERPRINT, "ttl_seconds": 60},
        ).json()
        wrong_device = client.post(
            "/v1/agent/enroll",
            json={
                "token": issued["token"],
                "fingerprint": "d" * 64,
                "hostname": "ATTACKER-PC",
                "agent_version": "0.1.0",
            },
        )
        assert wrong_device.status_code == 401

        request = {
            "token": issued["token"],
            "fingerprint": FINGERPRINT,
            "hostname": "TEST-PC",
            "agent_version": "0.1.0",
        }
        first = client.post("/v1/agent/enroll", json=request)
        replay = client.post("/v1/agent/enroll", json=request)

    assert first.status_code == 200
    assert replay.status_code == 409


def test_reenrollment_preserves_device_id_and_rotates_credential(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(create_app()) as client:
        first = enroll(client)
        second = enroll(client)
        assert second["device_id"] == first["device_id"]
        assert second["device_credential"] != first["device_credential"]

        inventory = {
            "collected_at": datetime.now(UTC).isoformat(),
            "os": {"caption": "Windows"},
            "hardware": {"memory_bytes": 1024},
            "disks": [],
            "installed_software": [],
        }
        old_credential = client.put(
            "/v1/agent/inventory",
            headers={"Authorization": f"Bearer {first['device_credential']}"},
            json=inventory,
        )
        new_credential = client.put(
            "/v1/agent/inventory",
            headers={"Authorization": f"Bearer {second['device_credential']}"},
            json=inventory,
        )

    assert old_credential.status_code == 401
    assert new_credential.status_code == 200


def test_websocket_execution_round_trip(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(create_app()) as client:
        identity = enroll(client)
        with client.websocket_connect(
            "/v1/agent/connect",
            headers={"Authorization": f"Bearer {identity['device_credential']}"},
        ) as websocket:
            connected = websocket.receive_json()
            assert connected["type"] == "connected"

            devices = client.get("/v1/devices", headers=API_HEADERS).json()
            assert devices[0]["online"] is True

            accepted = client.post(
                f"/v1/devices/{identity['device_id']}/executions",
                headers={**API_HEADERS, "Idempotency-Key": "websocket-test"},
                json={"script": "Write-Output 'hello'", "timeout_seconds": 10},
            )
            assert accepted.status_code == 202, accepted.text
            job = websocket.receive_json()
            assert job["type"] == "execute"
            assert job["executionId"] == accepted.json()["execution_id"]

            websocket.send_json(
                {
                    "protocolVersion": 1,
                    "type": "state",
                    "executionId": job["executionId"],
                    "status": "running",
                }
            )
            websocket.send_json(
                {
                    "protocolVersion": 1,
                    "type": "result",
                    "executionId": job["executionId"],
                    "status": "succeeded",
                    "exitCode": 0,
                    "stdout": "hello\r\n",
                    "stderr": "",
                    "stdoutTruncated": False,
                    "stderrTruncated": False,
                    "durationMs": 25,
                    "executedSha256": job["scriptSha256"],
                }
            )
            websocket.send_json({"protocolVersion": 1, "type": "heartbeat"})
            assert websocket.receive_json()["type"] == "heartbeat_ack"

            result = client.get(f"/v1/executions/{job['executionId']}", headers=API_HEADERS).json()

    assert result["status"] == "succeeded"
    assert result["result"]["exit_code"] == 0
    assert result["result"]["stdout"] == "hello\r\n"
    assert result["result"]["executed_sha256"] == result["script_sha256"]


def test_structured_event_log_query_is_idempotent_and_bounded(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(create_app()) as client:
        identity = enroll(client)
        headers = {**API_HEADERS, "Idempotency-Key": "events-demo"}
        body = {
            "logs": ["System"],
            "severities": ["critical", "error", "warning"],
            "provider_names": ["Service Control Manager"],
            "event_ids": [7031],
            "max_events": 25,
            "timeout_seconds": 20,
            "delivery_timeout_seconds": 10,
        }
        first = client.post(
            f"/v1/devices/{identity['device_id']}/event-log-queries",
            headers=headers,
            json=body,
        )
        duplicate = client.post(
            f"/v1/devices/{identity['device_id']}/event-log-queries",
            headers=headers,
            json=body,
        )
        assert first.status_code == 202, first.text
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["query_id"] == first.json()["query_id"]

        query_id = UUID(first.json()["query_id"])
        with client.app.state.database.session_factory.begin() as session:
            execution = session.get(Execution, query_id)
            assert execution is not None
            assert execution.script.startswith("# squash:event-log-query:v1:")
            assert "Service Control Manager" not in execution.script.splitlines()[0]
            execution.status = "succeeded"
            execution.completed_at = datetime.now(UTC)
            execution.exit_code = 0
            execution.duration_ms = 42
            execution.stdout = json.dumps(
                [
                    {
                        "record_id": 123,
                        "log_name": "System",
                        "provider_name": "Service Control Manager",
                        "event_id": 7031,
                        "level": 2,
                        "level_name": "Error",
                        "time_created": datetime.now(UTC).isoformat(),
                        "machine_name": "TEST-PC",
                        "message": "A service terminated unexpectedly.",
                    }
                ]
            )

        result = client.get(
            f"/v1/event-log-queries/{query_id}",
            headers=API_HEADERS,
        )
        invalid = client.post(
            f"/v1/devices/{identity['device_id']}/event-log-queries",
            headers={**API_HEADERS, "Idempotency-Key": "invalid-events"},
            json={"logs": ["Security"]},
        )

    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded"
    assert result.json()["duration_ms"] == 42
    assert result.json()["events"][0]["event_id"] == 7031
    assert result.json()["events"][0]["message"] == "A service terminated unexpectedly."
    assert invalid.status_code == 422


def test_revocation_disconnects_and_rejects_device_credential(monkeypatch) -> None:
    configure(monkeypatch)
    with TestClient(create_app()) as client:
        identity = enroll(client)
        revoke = client.post(f"/v1/devices/{identity['device_id']}/revoke", headers=API_HEADERS)
        assert revoke.status_code == 200
        assert revoke.json()["revoked"] is True

        rejected = client.put(
            "/v1/agent/inventory",
            headers={"Authorization": f"Bearer {identity['device_credential']}"},
            json={
                "collected_at": datetime.now(UTC).isoformat(),
                "os": {},
                "hardware": {},
            },
        )

    assert rejected.status_code == 401


def test_offline_job_reaches_delivery_expired(monkeypatch) -> None:
    configure(monkeypatch)
    app = create_app()
    with TestClient(app) as client:
        identity = enroll(client)
        accepted = client.post(
            f"/v1/devices/{identity['device_id']}/executions",
            headers={**API_HEADERS, "Idempotency-Key": "offline-expiry"},
            json={"script": "Get-Date", "delivery_timeout_seconds": 30},
        )
        execution_id = accepted.json()["execution_id"]
        with app.state.database.session_factory.begin() as session:
            execution = session.get(Execution, UUID(execution_id))
            execution.delivery_deadline = datetime.now(UTC) - timedelta(seconds=1)

        assert expire_jobs(app.state.database, 15) == 1
        result = client.get(f"/v1/executions/{execution_id}", headers=API_HEADERS).json()

    assert result["status"] == "delivery_expired"
    assert result["completed_at"] is not None


def test_control_plane_bounds_agent_output(monkeypatch) -> None:
    configure(monkeypatch)
    monkeypatch.setenv("SQUASH_OUTPUT_LIMIT_BYTES_PER_STREAM", "8")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        identity = enroll(client)
        with client.websocket_connect(
            "/v1/agent/connect",
            headers={"Authorization": f"Bearer {identity['device_credential']}"},
        ) as websocket:
            websocket.receive_json()
            accepted = client.post(
                f"/v1/devices/{identity['device_id']}/executions",
                headers={**API_HEADERS, "Idempotency-Key": "bounded-output"},
                json={"script": "Write-Output '0123456789'"},
            )
            job = websocket.receive_json()
            websocket.send_json(
                {
                    "protocolVersion": 1,
                    "type": "state",
                    "executionId": job["executionId"],
                    "status": "running",
                }
            )
            websocket.send_json(
                {
                    "protocolVersion": 1,
                    "type": "result",
                    "executionId": job["executionId"],
                    "status": "succeeded",
                    "exitCode": 0,
                    "stdout": "0123456789abcdef",
                    "stderr": "",
                    "durationMs": 10,
                    "executedSha256": job["scriptSha256"],
                }
            )
            websocket.send_json({"protocolVersion": 1, "type": "heartbeat"})
            websocket.receive_json()
            result = client.get(
                f"/v1/executions/{accepted.json()['execution_id']}", headers=API_HEADERS
            ).json()

    assert result["result"]["stdout"] == "01234567"
    assert result["result"]["stdout_truncated"] is True
