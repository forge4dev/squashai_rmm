import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, update

from .agent_auth import hash_secret
from .audit import add_audit_event
from .auth import Caller
from .database import SessionDependency
from .models import Device, DeviceCredential, EnrollmentToken
from .schemas import (
    AgentEnrollRequest,
    AgentEnrollResponse,
    EnrollmentTokenCreate,
    EnrollmentTokenIssued,
)

router = APIRouter(prefix="/v1", tags=["enrollment"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@router.post(
    "/enrollment-tokens",
    response_model=EnrollmentTokenIssued,
    status_code=status.HTTP_201_CREATED,
)
def create_enrollment_token(
    body: EnrollmentTokenCreate,
    caller: Caller,
    session: SessionDependency,
) -> EnrollmentTokenIssued:
    plaintext = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    record = EnrollmentToken(
        token_hash=hash_secret(plaintext),
        expected_fingerprint=body.expected_fingerprint,
        created_by=caller,
        created_at=now,
        expires_at=now + timedelta(seconds=body.ttl_seconds),
    )
    session.add(record)
    session.flush()
    add_audit_event(
        session,
        "enrollment_token.created",
        caller,
        details={"token_id": str(record.id), "expected_fingerprint": body.expected_fingerprint},
    )
    session.commit()
    return EnrollmentTokenIssued(
        id=record.id,
        token=plaintext,
        expected_fingerprint=record.expected_fingerprint,
        expires_at=record.expires_at,
    )


@router.post("/agent/enroll", response_model=AgentEnrollResponse)
def enroll_agent(
    body: AgentEnrollRequest,
    session: SessionDependency,
    request: Request,
) -> AgentEnrollResponse:
    now = datetime.now(UTC)
    token = session.scalar(
        select(EnrollmentToken).where(EnrollmentToken.token_hash == hash_secret(body.token))
    )
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid enrollment token")
    if token.used_at is not None:
        raise HTTPException(status_code=409, detail="Enrollment token has already been used")
    if _as_utc(token.expires_at) <= now:
        raise HTTPException(status_code=401, detail="Enrollment token has expired")
    if not secrets.compare_digest(token.expected_fingerprint, body.fingerprint):
        raise HTTPException(status_code=401, detail="Enrollment token is not valid for this device")

    consumed = session.execute(
        update(EnrollmentToken)
        .where(EnrollmentToken.id == token.id, EnrollmentToken.used_at.is_(None))
        .values(used_at=now)
    )
    if consumed.rowcount != 1:
        session.rollback()
        raise HTTPException(status_code=409, detail="Enrollment token has already been used")

    device = session.scalar(select(Device).where(Device.fingerprint == body.fingerprint))
    if device is None:
        device = Device(
            fingerprint=body.fingerprint,
            hostname=body.hostname,
            agent_version=body.agent_version,
            created_at=now,
        )
        session.add(device)
        session.flush()
    else:
        device.hostname = body.hostname
        device.agent_version = body.agent_version
        device.revoked_at = None
        session.execute(
            update(DeviceCredential)
            .where(
                DeviceCredential.device_id == device.id,
                DeviceCredential.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    plaintext_credential = secrets.token_urlsafe(48)
    credential = DeviceCredential(
        device_id=device.id,
        token_hash=hash_secret(plaintext_credential),
        issued_at=now,
    )
    session.add(credential)
    add_audit_event(
        session,
        "device.enrolled",
        "enrollment-token",
        device_id=device.id,
        details={"credential_id": str(credential.id), "agent_version": body.agent_version},
    )
    session.commit()

    websocket_url = (
        request.app.state.settings.public_base_url.rstrip("/")
        + request.app.state.settings.websocket_url
    )
    websocket_url = websocket_url.replace("https://", "wss://").replace("http://", "ws://")
    return AgentEnrollResponse(
        device_id=device.id,
        device_credential=plaintext_credential,
        websocket_url=websocket_url,
    )
