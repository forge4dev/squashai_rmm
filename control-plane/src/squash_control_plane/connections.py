import asyncio
from datetime import UTC, datetime
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from .audit import add_audit_event
from .database import Database
from .models import Execution
from .schemas import ExecutionStatus


class ConnectionManager:
    def __init__(self, database: Database, output_limit_bytes_per_stream: int) -> None:
        self._database = database
        self._output_limit = output_limit_bytes_per_stream
        self._connections: dict[UUID, WebSocket] = {}
        self._lock = asyncio.Lock()

    @property
    def output_limit_bytes_per_stream(self) -> int:
        return self._output_limit

    async def register(self, device_id: UUID, websocket: WebSocket) -> None:
        previous: WebSocket | None
        async with self._lock:
            previous = self._connections.get(device_id)
            self._connections[device_id] = websocket
        if previous is not None and previous is not websocket:
            await previous.close(code=4001, reason="Replaced by a newer connection")

    async def unregister(self, device_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            if self._connections.get(device_id) is websocket:
                self._connections.pop(device_id, None)

    def is_online(self, device_id: UUID) -> bool:
        return device_id in self._connections

    async def disconnect(self, device_id: UUID, reason: str) -> None:
        async with self._lock:
            websocket = self._connections.pop(device_id, None)
        if websocket is not None:
            await websocket.close(code=4003, reason=reason)

    async def dispatch_execution(self, execution_id: UUID) -> bool:
        with self._database.session_factory.begin() as session:
            execution = session.get(Execution, execution_id)
            if execution is None or execution.status not in {
                ExecutionStatus.QUEUED.value,
                ExecutionStatus.DISPATCHED.value,
            }:
                return False
            websocket = self._connections.get(execution.device_id)
            if websocket is None:
                return False
            if execution.status == ExecutionStatus.QUEUED.value:
                execution.status = ExecutionStatus.DISPATCHED.value
                execution.dispatched_at = datetime.now(UTC)
                add_audit_event(
                    session,
                    "execution.dispatched",
                    "control-plane",
                    device_id=execution.device_id,
                    execution_id=execution.id,
                )
            message = {
                "protocolVersion": 1,
                "type": "execute",
                "executionId": str(execution.id),
                "script": execution.script,
                "scriptSha256": execution.script_sha256,
                "timeoutSeconds": execution.timeout_seconds,
                "outputLimitBytesPerStream": self._output_limit,
            }

        try:
            await websocket.send_json(message)
        except (OSError, RuntimeError, WebSocketDisconnect):
            await self.unregister(execution.device_id, websocket)
            return False
        return True

    async def dispatch_pending(self, device_id: UUID) -> None:
        with self._database.session_factory() as session:
            execution_ids = list(
                session.scalars(
                    select(Execution.id)
                    .where(
                        Execution.device_id == device_id,
                        Execution.status.in_(
                            [
                                ExecutionStatus.QUEUED.value,
                                ExecutionStatus.DISPATCHED.value,
                            ]
                        ),
                    )
                    .order_by(Execution.created_at)
                )
            )
        for execution_id in execution_ids:
            if not await self.dispatch_execution(execution_id):
                break
