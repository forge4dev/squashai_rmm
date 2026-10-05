from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .models import AuditEvent


def add_audit_event(
    session: Session,
    event_type: str,
    actor_id: str,
    *,
    device_id: UUID | None = None,
    execution_id: UUID | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            event_type=event_type,
            actor_id=actor_id,
            device_id=device_id,
            execution_id=execution_id,
            details=details or {},
        )
    )
