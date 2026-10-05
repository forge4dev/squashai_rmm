from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from .auth import Caller
from .database import SessionDependency
from .models import AuditEvent
from .schemas import AuditEventRead

router = APIRouter(prefix="/v1/audit-events", tags=["audit"])


@router.get("", response_model=list[AuditEventRead])
def list_audit_events(
    caller: Caller,
    session: SessionDependency,
    device_id: UUID | None = None,
    execution_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[AuditEventRead]:
    query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
    if device_id is not None:
        query = query.where(AuditEvent.device_id == device_id)
    if execution_id is not None:
        query = query.where(AuditEvent.execution_id == execution_id)
    return [
        AuditEventRead(
            id=event.id,
            event_type=event.event_type,
            actor_id=event.actor_id,
            device_id=event.device_id,
            execution_id=event.execution_id,
            occurred_at=event.occurred_at,
            details=event.details,
        )
        for event in session.scalars(query)
    ]
