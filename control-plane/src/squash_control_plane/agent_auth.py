import hashlib
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from .database import SessionDependency
from .models import Device, DeviceCredential

device_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class DevicePrincipal:
    device_id: UUID
    credential_id: UUID


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def require_device(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(device_bearer)],
    session: SessionDependency,
) -> DevicePrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Device authentication required"
        )

    credential = session.scalar(
        select(DeviceCredential).where(
            DeviceCredential.token_hash == hash_secret(credentials.credentials),
            DeviceCredential.revoked_at.is_(None),
        )
    )
    if credential is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credentials"
        )
    device = session.get(Device, credential.device_id)
    if device is None or device.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Device is revoked")
    return DevicePrincipal(device_id=device.id, credential_id=credential.id)


AuthenticatedDevice = Annotated[DevicePrincipal, Depends(require_device)]
