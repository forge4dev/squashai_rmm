import secrets
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from .agent_auth import hash_secret
from .audit import add_audit_event
from .auth import Caller
from .database import SessionDependency
from .models import ApiClient
from .schemas import ApiClientCreate, ApiClientIssued, ApiClientRead

router = APIRouter(prefix="/v1/api-clients", tags=["api-clients"])


@router.post("", response_model=ApiClientIssued, status_code=status.HTTP_201_CREATED)
def create_api_client(
    body: ApiClientCreate,
    caller: Caller,
    session: SessionDependency,
) -> ApiClientIssued:
    plaintext = secrets.token_urlsafe(48)
    record = ApiClient(name=body.name, key_hash=hash_secret(plaintext), created_by=caller)
    session.add(record)
    session.flush()
    add_audit_event(
        session,
        "api_client.created",
        caller,
        details={"api_client_id": str(record.id), "name": record.name},
    )
    session.commit()
    return ApiClientIssued(
        id=record.id,
        name=record.name,
        api_key=plaintext,
        created_at=record.created_at,
    )


@router.get("", response_model=list[ApiClientRead])
def list_api_clients(caller: Caller, session: SessionDependency) -> list[ApiClientRead]:
    return [
        ApiClientRead(
            id=item.id,
            name=item.name,
            created_at=item.created_at,
            revoked_at=item.revoked_at,
        )
        for item in session.scalars(select(ApiClient).order_by(ApiClient.created_at))
    ]


@router.post("/{client_id}/revoke", response_model=ApiClientRead)
def revoke_api_client(
    client_id: UUID,
    caller: Caller,
    session: SessionDependency,
) -> ApiClientRead:
    client = session.get(ApiClient, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="API client not found")
    if client.revoked_at is None:
        client.revoked_at = datetime.now(UTC)
        add_audit_event(
            session,
            "api_client.revoked",
            caller,
            details={"api_client_id": str(client.id), "name": client.name},
        )
        session.commit()
    return ApiClientRead(
        id=client.id,
        name=client.name,
        created_at=client.created_at,
        revoked_at=client.revoked_at,
    )
