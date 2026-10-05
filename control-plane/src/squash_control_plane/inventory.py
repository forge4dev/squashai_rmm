from datetime import UTC, datetime

from fastapi import APIRouter

from .agent_auth import AuthenticatedDevice
from .audit import add_audit_event
from .database import SessionDependency
from .models import Device, DeviceInventory
from .schemas import InventoryRead, InventoryWrite

router = APIRouter(prefix="/v1/agent", tags=["agent-inventory"])


@router.put("/inventory", response_model=InventoryRead)
def put_inventory(
    body: InventoryWrite,
    device_principal: AuthenticatedDevice,
    session: SessionDependency,
) -> InventoryRead:
    now = datetime.now(UTC)
    payload = body.model_dump(mode="json", exclude={"collected_at"})
    inventory = session.get(DeviceInventory, device_principal.device_id)
    if inventory is None:
        inventory = DeviceInventory(
            device_id=device_principal.device_id,
            collected_at=body.collected_at,
            received_at=now,
            payload=payload,
        )
        session.add(inventory)
    else:
        inventory.collected_at = body.collected_at
        inventory.received_at = now
        inventory.payload = payload
    device = session.get(Device, device_principal.device_id)
    if device is not None:
        device.last_seen_at = now
    add_audit_event(
        session,
        "inventory.updated",
        f"device:{device_principal.device_id}",
        device_id=device_principal.device_id,
        details={
            "software_count": len(body.installed_software),
            "disk_count": len(body.disks),
        },
    )
    session.commit()
    return InventoryRead(
        device_id=device_principal.device_id,
        collected_at=inventory.collected_at,
        received_at=inventory.received_at,
        **inventory.payload,
    )
