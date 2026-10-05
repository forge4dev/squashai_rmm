import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .audit import add_audit_event
from .auth import Caller
from .connections import ConnectionManager
from .database import Database, SessionDependency
from .models import Device, Execution
from .schemas import (
    ExecutionAccepted,
    ExecutionCreate,
    ExecutionRead,
    ExecutionResult,
    ExecutionStatus,
)

router = APIRouter(prefix="/v1", tags=["executions"])


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _request_hash(device_id: UUID, request: ExecutionCreate) -> str:
    canonical = json.dumps(
        {
            "device_id": str(device_id),
            "script": request.script,
            "timeout_seconds": request.timeout_seconds,
            "delivery_timeout_seconds": request.delivery_timeout_seconds,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return _hash_text(canonical)


def execution_to_read(execution: Execution) -> ExecutionRead:
    result = None
    if execution.completed_at is not None:
        result = ExecutionResult(
            exit_code=execution.exit_code,
            stdout=execution.stdout,
            stderr=execution.stderr,
            stdout_truncated=execution.stdout_truncated,
            stderr_truncated=execution.stderr_truncated,
            duration_ms=execution.duration_ms,
            executed_sha256=execution.executed_sha256,
        )
    return ExecutionRead(
        id=execution.id,
        device_id=execution.device_id,
        status=ExecutionStatus(execution.status),
        script=execution.script,
        script_sha256=execution.script_sha256,
        timeout_seconds=execution.timeout_seconds,
        delivery_deadline=execution.delivery_deadline,
        created_at=execution.created_at,
        dispatched_at=execution.dispatched_at,
        started_at=execution.started_at,
        completed_at=execution.completed_at,
        terminal_reason=execution.terminal_reason,
        result=result,
    )


@router.post(
    "/devices/{device_id}/executions",
    response_model=ExecutionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_execution(
    device_id: UUID,
    body: ExecutionCreate,
    caller: Caller,
    session: SessionDependency,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> ExecutionAccepted:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.revoked_at is not None:
        raise HTTPException(status_code=409, detail="Device is revoked")

    request_hash = _request_hash(device_id, body)
    existing = session.scalar(
        select(Execution).where(
            Execution.caller_id == caller,
            Execution.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_sha256 != request_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key was already used for a different request",
            )
        return ExecutionAccepted(
            execution_id=existing.id,
            status=ExecutionStatus(existing.status),
            status_url=str(request.url_for("get_execution", execution_id=existing.id)),
        )

    now = datetime.now(UTC)
    execution = Execution(
        device_id=device.id,
        caller_id=caller,
        idempotency_key=idempotency_key,
        request_sha256=request_hash,
        script=body.script,
        script_sha256=_hash_text(body.script),
        timeout_seconds=body.timeout_seconds,
        delivery_deadline=now + timedelta(seconds=body.delivery_timeout_seconds),
        created_at=now,
    )
    session.add(execution)
    session.flush()
    add_audit_event(
        session,
        "execution.created",
        caller,
        device_id=device.id,
        execution_id=execution.id,
        details={
            "script_sha256": execution.script_sha256,
            "timeout_seconds": body.timeout_seconds,
            "delivery_timeout_seconds": body.delivery_timeout_seconds,
        },
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(Execution).where(
                Execution.caller_id == caller,
                Execution.idempotency_key == idempotency_key,
            )
        )
        if existing is None or existing.request_sha256 != request_hash:
            raise HTTPException(status_code=409, detail="Conflicting concurrent request") from None
        execution = existing

    manager: ConnectionManager = request.app.state.connections
    await manager.dispatch_execution(execution.id)
    session.refresh(execution)
    return ExecutionAccepted(
        execution_id=execution.id,
        status=ExecutionStatus(execution.status),
        status_url=str(request.url_for("get_execution", execution_id=execution.id)),
    )


@router.get("/executions/{execution_id}", response_model=ExecutionRead, name="get_execution")
def get_execution(
    execution_id: UUID,
    caller: Caller,
    session: SessionDependency,
) -> ExecutionRead:
    execution = session.get(Execution, execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found")
    return execution_to_read(execution)


@router.get("/executions", response_model=list[ExecutionRead])
def list_executions(
    caller: Caller,
    session: SessionDependency,
    device_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ExecutionRead]:
    query = select(Execution).order_by(Execution.created_at.desc()).limit(limit)
    if device_id is not None:
        query = query.where(Execution.device_id == device_id)
    return [execution_to_read(item) for item in session.scalars(query)]


def expire_jobs(database: Database, execution_grace_seconds: int) -> int:
    now = datetime.now(UTC)
    changed = 0
    with database.session_factory.begin() as session:
        pending = session.scalars(
            select(Execution).where(
                Execution.status.in_(
                    [ExecutionStatus.QUEUED.value, ExecutionStatus.DISPATCHED.value]
                )
            )
        )
        for execution in pending:
            if _as_utc(execution.delivery_deadline) > now:
                continue
            execution.status = ExecutionStatus.DELIVERY_EXPIRED.value
            execution.completed_at = now
            execution.terminal_reason = "Device did not accept the job before its delivery deadline"
            add_audit_event(
                session,
                "execution.delivery_expired",
                "control-plane",
                device_id=execution.device_id,
                execution_id=execution.id,
            )
            changed += 1

        running = session.scalars(
            select(Execution).where(Execution.status == ExecutionStatus.RUNNING.value)
        )
        for execution in running:
            if execution.started_at is None:
                continue
            deadline = _as_utc(execution.started_at) + timedelta(
                seconds=execution.timeout_seconds + execution_grace_seconds
            )
            if deadline > now:
                continue
            execution.status = ExecutionStatus.TIMED_OUT.value
            execution.completed_at = now
            execution.terminal_reason = "Agent did not report completion before the deadline"
            add_audit_event(
                session,
                "execution.control_plane_timeout",
                "control-plane",
                device_id=execution.device_id,
                execution_id=execution.id,
            )
            changed += 1
    return changed
