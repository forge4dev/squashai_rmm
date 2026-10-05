import hashlib

import httpx
import pytest

from squash_ai_driver.control_plane import ControlPlaneClient, ControlPlaneError

SCRIPT = "Get-Date"
SCRIPT_HASH = hashlib.sha256(SCRIPT.encode()).hexdigest()


def test_selects_a_device_and_waits_for_structured_result() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/devices":
            return httpx.Response(
                200,
                json=[{"id": "device-1", "hostname": "WIN-1", "online": True, "revoked": False}],
            )
        if request.method == "POST":
            return httpx.Response(202, json={"execution_id": "execution-1"})
        return httpx.Response(
            200,
            json={
                "id": "execution-1",
                "status": "succeeded",
                "script_sha256": SCRIPT_HASH,
                "result": {
                    "exit_code": 0,
                    "stdout": "ok",
                    "stderr": "",
                    "executed_sha256": SCRIPT_HASH,
                },
            },
        )

    with ControlPlaneClient(
        "https://rmm.test",
        "secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        assert client.select_device(None)["id"] == "device-1"
        result, elapsed = client.run_powershell(
            "device-1",
            SCRIPT,
            timeout_seconds=10,
            delivery_timeout_seconds=5,
            poll_interval_seconds=0,
        )

    assert result["result"]["stdout"] == "ok"
    assert elapsed >= 0
    assert requests[0].headers["x-api-key"] == "secret"
    assert requests[1].headers["idempotency-key"].startswith("ai-driver-")


def test_rejects_executed_script_hash_mismatch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"execution_id": "execution-1"})
        return httpx.Response(
            200,
            json={
                "id": "execution-1",
                "status": "succeeded",
                "script_sha256": SCRIPT_HASH,
                "result": {"executed_sha256": "0" * 64},
            },
        )

    with (
        ControlPlaneClient(
            "https://rmm.test", "secret", transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(ControlPlaneError, match="Agent-reported script hash"),
    ):
        client.run_powershell(
            "device-1",
            SCRIPT,
            timeout_seconds=10,
            delivery_timeout_seconds=5,
            poll_interval_seconds=0,
        )


def test_requires_https_by_default() -> None:
    with pytest.raises(ValueError, match="must use HTTPS"):
        ControlPlaneClient("http://rmm.test", "secret")


def test_reads_inventory_and_waits_for_structured_event_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/devices/device-1/inventory":
            return httpx.Response(200, json={"os": {"caption": "Windows 11"}})
        if request.method == "POST":
            return httpx.Response(202, json={"query_id": "query-1"})
        return httpx.Response(
            200,
            json={
                "query_id": "query-1",
                "status": "succeeded",
                "events": [{"event_id": 7031, "message": "Service stopped"}],
            },
        )

    filters = {
        "logs": ["System"],
        "severities": ["error"],
        "timeout_seconds": 30,
        "delivery_timeout_seconds": 15,
    }
    with ControlPlaneClient(
        "https://rmm.test",
        "secret",
        transport=httpx.MockTransport(handler),
    ) as client:
        inventory = client.get_inventory("device-1")
        result, elapsed = client.query_event_logs(
            "device-1", filters, poll_interval_seconds=0
        )

    assert inventory["os"]["caption"] == "Windows 11"
    assert result["events"][0]["event_id"] == 7031
    assert elapsed >= 0
    assert requests[1].headers["idempotency-key"].startswith("ai-events-")
    assert requests[1].url.path == "/v1/devices/device-1/event-log-queries"
    assert requests[2].url.path == "/v1/event-log-queries/query-1"
