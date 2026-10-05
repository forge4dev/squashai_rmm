from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select, update

from .audit import add_audit_event
from .auth import Caller
from .connections import ConnectionManager
from .database import SessionDependency
from .models import Device, DeviceCredential, DeviceInventory
from .schemas import DeviceRead, InventoryRead

router = APIRouter(prefix="/v1/devices", tags=["devices"])


def _to_read(device: Device, manager: ConnectionManager) -> DeviceRead:
    return DeviceRead(
        id=device.id,
        hostname=device.hostname,
        fingerprint=device.fingerprint,
        agent_version=device.agent_version,
        online=manager.is_online(device.id),
        revoked=device.revoked_at is not None,
        last_seen_at=device.last_seen_at,
        created_at=device.created_at,
    )


@router.get("", response_model=list[DeviceRead])
def list_devices(
    caller: Caller,
    session: SessionDependency,
    request: Request,
) -> list[DeviceRead]:
    manager: ConnectionManager = request.app.state.connections
    devices = session.scalars(select(Device).order_by(Device.hostname)).all()
    return [_to_read(device, manager) for device in devices]


@router.get("/{device_id}", response_model=DeviceRead)
def get_device(
    device_id: UUID,
    caller: Caller,
    session: SessionDependency,
    request: Request,
) -> DeviceRead:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return _to_read(device, request.app.state.connections)


@router.post("/{device_id}/revoke", response_model=DeviceRead)
async def revoke_device(
    device_id: UUID,
    caller: Caller,
    session: SessionDependency,
    request: Request,
) -> DeviceRead:
    device = session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    now = datetime.now(UTC)
    if device.revoked_at is None:
        device.revoked_at = now
        session.execute(
            update(DeviceCredential)
            .where(
                DeviceCredential.device_id == device.id,
                DeviceCredential.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        add_audit_event(session, "device.revoked", caller, device_id=device.id)
        session.commit()
    manager: ConnectionManager = request.app.state.connections
    await manager.disconnect(device.id, "Device credentials revoked")
    return _to_read(device, manager)


@router.get("/{device_id}/inventory", response_model=InventoryRead)
def get_inventory(
    device_id: UUID,
    caller: Caller,
    session: SessionDependency,
) -> InventoryRead:
    inventory = session.get(DeviceInventory, device_id)
    if inventory is None:
        raise HTTPException(status_code=404, detail="Inventory not found")
    return InventoryRead(
        device_id=device_id,
        collected_at=inventory.collected_at,
        received_at=inventory.received_at,
        **inventory.payload,
    )
