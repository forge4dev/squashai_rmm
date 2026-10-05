from __future__ import annotations

import hashlib
import time
from typing import Any, Self
from uuid import uuid4

import httpx

TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "timed_out", "delivery_expired", "agent_interrupted"}
)


class ControlPlaneError(RuntimeError):
    """Raised when the control plane cannot safely complete an operation."""


class ControlPlaneClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        ca_file: str | None = None,
        allow_http: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized_url = base_url.rstrip("/")
        if not normalized_url.startswith("https://") and not allow_http:
            raise ValueError(
                "The control-plane URL must use HTTPS (or pass --allow-http for local dev)"
            )
        if not api_key:
            raise ValueError("The control-plane API key is required")

        self._client = httpx.Client(
            base_url=normalized_url,
            headers={"X-API-Key": api_key, "User-Agent": "squash-ai-driver/0.1"},
            verify=ca_file if ca_file else True,
            timeout=httpx.Timeout(10.0, connect=5.0),
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def list_devices(self) -> list[dict[str, Any]]:
        response = self._request("GET", "/v1/devices")
        body = response.json()
        if not isinstance(body, list):
            raise ControlPlaneError("Control plane returned an invalid device list")
        return body

    def get_inventory(self, device_id: str) -> dict[str, Any]:
        response = self._request("GET", f"/v1/devices/{device_id}/inventory")
        body = response.json()
        if not isinstance(body, dict):
            raise ControlPlaneError("Control plane returned invalid inventory data")
        return body

    def select_device(self, selector: str | None) -> dict[str, Any]:
        devices = [device for device in self.list_devices() if not device.get("revoked", False)]
        if selector:
            matches = [
                device
                for device in devices
                if str(device.get("id", "")).casefold() == selector.casefold()
                or str(device.get("hostname", "")).casefold() == selector.casefold()
            ]
            if not matches:
                raise ControlPlaneError(f"No non-revoked device matches {selector!r}")
            if len(matches) > 1:
                raise ControlPlaneError(f"Device selector {selector!r} is ambiguous; use its UUID")
            return matches[0]

        if len(devices) != 1:
            choices = (
                ", ".join(f"{device.get('hostname')} ({device.get('id')})" for device in devices)
                or "none"
            )
            raise ControlPlaneError(
                f"--device is required when there is not exactly one device; choices: {choices}"
            )
        return devices[0]

    def run_powershell(
        self,
        device_id: str,
        script: str,
        *,
        timeout_seconds: int,
        delivery_timeout_seconds: int,
        poll_interval_seconds: float = 0.2,
    ) -> tuple[dict[str, Any], float]:
        idempotency_key = f"ai-driver-{uuid4()}"
        started = time.monotonic()
        accepted = self._request(
            "POST",
            f"/v1/devices/{device_id}/executions",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "script": script,
                "timeout_seconds": timeout_seconds,
                "delivery_timeout_seconds": delivery_timeout_seconds,
            },
        ).json()
        execution_id = accepted.get("execution_id")
        if not execution_id:
            raise ControlPlaneError("Control plane accepted the job without an execution ID")

        local_deadline = started + delivery_timeout_seconds + timeout_seconds + 30
        while True:
            execution = self._request("GET", f"/v1/executions/{execution_id}").json()
            status = execution.get("status")
            if status in TERMINAL_STATUSES:
                self._verify_script_hash(execution, script)
                return execution, (time.monotonic() - started) * 1000
            if time.monotonic() >= local_deadline:
                raise ControlPlaneError(
                    f"Local wait expired for execution {execution_id}; query its status directly"
                )
            time.sleep(poll_interval_seconds)

    def query_event_logs(
        self,
        device_id: str,
        filters: dict[str, Any],
        *,
        poll_interval_seconds: float = 0.2,
    ) -> tuple[dict[str, Any], float]:
        idempotency_key = f"ai-events-{uuid4()}"
        started = time.monotonic()
        accepted = self._request(
            "POST",
            f"/v1/devices/{device_id}/event-log-queries",
            headers={"Idempotency-Key": idempotency_key},
            json=filters,
        ).json()
        query_id = accepted.get("query_id")
        if not query_id:
            raise ControlPlaneError("Control plane accepted the query without a query ID")

        delivery_timeout = int(filters.get("delivery_timeout_seconds", 15))
        execution_timeout = int(filters.get("timeout_seconds", 30))
        local_deadline = started + delivery_timeout + execution_timeout + 30
        while True:
            query = self._request("GET", f"/v1/event-log-queries/{query_id}").json()
            if query.get("status") in TERMINAL_STATUSES:
                return query, (time.monotonic() - started) * 1000
            if time.monotonic() >= local_deadline:
                raise ControlPlaneError(
                    f"Local wait expired for event-log query {query_id}; query it directly"
                )
            time.sleep(poll_interval_seconds)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail", exc.response.text)
            except (ValueError, AttributeError):
                detail = exc.response.text
            raise ControlPlaneError(
                f"Control plane returned HTTP {exc.response.status_code}: {detail}"
            ) from exc
        except httpx.HTTPError as exc:
            raise ControlPlaneError(f"Cannot reach the control plane: {exc}") from exc

    @staticmethod
    def _verify_script_hash(execution: dict[str, Any], script: str) -> None:
        expected_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
        if execution.get("script_sha256") != expected_hash:
            raise ControlPlaneError(
                "Control-plane script hash does not match the dispatched script"
            )
        result = execution.get("result") or {}
        executed_hash = result.get("executed_sha256")
        if executed_hash is not None and executed_hash != expected_hash:
            raise ControlPlaneError(
                "Agent-reported script hash does not match the dispatched script"
            )
