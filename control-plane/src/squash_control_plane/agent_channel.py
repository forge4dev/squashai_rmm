import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from .agent_auth import hash_secret
from .audit import add_audit_event
from .connections import ConnectionManager
from .database import Database
from .models import Device, DeviceCredential, Execution
from .schemas import TERMINAL_EXECUTION_STATUSES, ExecutionStatus

router = APIRouter(tags=["agent-channel"])


class AgentStateMessage(BaseModel):
    protocolVersion: int = Field(alias="protocolVersion")
    type: str
    executionId: UUID
    status: ExecutionStatus


class AgentResultMessage(BaseModel):
    protocolVersion: int
    type: str
    executionId: UUID
    status: ExecutionStatus
    exitCode: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdoutTruncated: bool = False
    stderrTruncated: bool = False
    durationMs: int = Field(ge=0)
    executedSha256: str


def _device_from_authorization(database: Database, authorization: str | None) -> Device | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    secret = authorization[7:].strip()
    with database.session_factory() as session:
        credential = session.scalar(
            select(DeviceCredential).where(
                DeviceCredential.token_hash == hash_secret(secret),
                DeviceCredential.revoked_at.is_(None),
            )
        )
        if credential is None:
            return None
        device = session.get(Device, credential.device_id)
        if device is None or device.revoked_at is not None:
            return None
        session.expunge(device)
        return device


def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _record_heartbeat(database: Database, device_id: UUID) -> None:
    with database.session_factory.begin() as session:
        device = session.get(Device, device_id)
        if device is not None:
            device.last_seen_at = datetime.now(UTC)


def _record_running(database: Database, device_id: UUID, message: AgentStateMessage) -> None:
    if message.protocolVersion != 1 or message.type != "state":
        raise ValueError("Unsupported agent message")
    if message.status is not ExecutionStatus.RUNNING:
        raise ValueError("Only the running state is accepted from an agent")

    with database.session_factory.begin() as session:
        execution = session.get(Execution, message.executionId)
        if execution is None or execution.device_id != device_id:
            raise ValueError("Unknown execution")
        if execution.status == ExecutionStatus.RUNNING.value:
            return
        if execution.status != ExecutionStatus.DISPATCHED.value:
            raise ValueError("Invalid execution state transition")
        execution.status = ExecutionStatus.RUNNING.value
        execution.started_at = datetime.now(UTC)
        add_audit_event(
            session,
            "execution.running",
            f"device:{device_id}",
            device_id=device_id,
            execution_id=execution.id,
        )


def _record_result(
    database: Database,
    device_id: UUID,
    message: AgentResultMessage,
    output_limit: int,
) -> None:
    if message.protocolVersion != 1 or message.type != "result":
        raise ValueError("Unsupported agent message")
    if message.status not in TERMINAL_EXECUTION_STATUSES - {ExecutionStatus.DELIVERY_EXPIRED}:
        raise ValueError("Agent supplied a non-terminal result")

    with database.session_factory.begin() as session:
        execution = session.get(Execution, message.executionId)
        if execution is None or execution.device_id != device_id:
            raise ValueError("Unknown execution")
        if ExecutionStatus(execution.status) in TERMINAL_EXECUTION_STATUSES:
            return

        stdout, stdout_cut = _bounded_text(message.stdout, output_limit)
        stderr, stderr_cut = _bounded_text(message.stderr, output_limit)
        now = datetime.now(UTC)
        hash_matches = message.executedSha256 == execution.script_sha256
        execution.status = message.status.value if hash_matches else ExecutionStatus.FAILED.value
        execution.completed_at = now
        execution.exit_code = message.exitCode
        execution.stdout = stdout
        execution.stderr = stderr
        execution.stdout_truncated = message.stdoutTruncated or stdout_cut
        execution.stderr_truncated = message.stderrTruncated or stderr_cut
        execution.duration_ms = message.durationMs
        execution.executed_sha256 = message.executedSha256
        if not hash_matches:
            execution.terminal_reason = "Agent-reported script hash did not match dispatch"
        add_audit_event(
            session,
            "execution.completed" if hash_matches else "execution.hash_mismatch",
            f"device:{device_id}",
            device_id=device_id,
            execution_id=execution.id,
            details={
                "status": execution.status,
                "exit_code": message.exitCode,
                "duration_ms": message.durationMs,
                "stdout_bytes": len(stdout.encode("utf-8")),
                "stderr_bytes": len(stderr.encode("utf-8")),
            },
        )


@router.websocket("/v1/agent/connect")
async def agent_connect(websocket: WebSocket) -> None:
    database: Database = websocket.app.state.database
    manager: ConnectionManager = websocket.app.state.connections
    settings = websocket.app.state.settings
    device = _device_from_authorization(database, websocket.headers.get("authorization"))
    if device is None:
        await websocket.close(code=4401, reason="Device authentication failed")
        return

    await websocket.accept()
    await manager.register(device.id, websocket)
    _record_heartbeat(database, device.id)
    await websocket.send_json(
        {
            "protocolVersion": 1,
            "type": "connected",
            "deviceId": str(device.id),
            "heartbeatIntervalSeconds": settings.heartbeat_interval_seconds,
        }
    )
    await manager.dispatch_pending(device.id)

    try:
        while True:
            raw = await asyncio.wait_for(
                websocket.receive_json(), timeout=settings.heartbeat_grace_seconds
            )
            _record_heartbeat(database, device.id)
            message_type = raw.get("type") if isinstance(raw, dict) else None
            if message_type == "heartbeat":
                await websocket.send_json({"protocolVersion": 1, "type": "heartbeat_ack"})
            elif message_type == "state":
                _record_running(database, device.id, AgentStateMessage.model_validate(raw))
            elif message_type == "result":
                _record_result(
                    database,
                    device.id,
                    AgentResultMessage.model_validate(raw),
                    manager.output_limit_bytes_per_stream,
                )
                await manager.dispatch_pending(device.id)
            else:
                await websocket.send_json(
                    {"protocolVersion": 1, "type": "error", "code": "invalid_message"}
                )
    except (TimeoutError, WebSocketDisconnect):
        pass
    except (ValidationError, ValueError):
        await websocket.close(code=4400, reason="Invalid agent message")
    finally:
        await manager.unregister(device.id, websocket)
