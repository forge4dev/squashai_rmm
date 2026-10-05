from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExecutionStatus(StrEnum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    DELIVERY_EXPIRED = "delivery_expired"
    AGENT_INTERRUPTED = "agent_interrupted"


TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.DELIVERY_EXPIRED,
        ExecutionStatus.AGENT_INTERRUPTED,
    }
)


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "squash-control-plane"


class ApiClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class ApiClientIssued(BaseModel):
    id: UUID
    name: str
    api_key: str
    created_at: datetime


class ApiClientRead(BaseModel):
    id: UUID
    name: str
    created_at: datetime
    revoked_at: datetime | None


class EnrollmentTokenCreate(BaseModel):
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    ttl_seconds: int = Field(default=300, ge=30, le=900)


class EnrollmentTokenIssued(BaseModel):
    id: UUID
    token: str
    expected_fingerprint: str
    expires_at: datetime


class AgentEnrollRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    hostname: str = Field(min_length=1, max_length=255)
    agent_version: str = Field(min_length=1, max_length=64)


class AgentEnrollResponse(BaseModel):
    device_id: UUID
    device_credential: str
    websocket_url: str


class ExecutionCreate(BaseModel):
    script: str
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    delivery_timeout_seconds: int = Field(default=30, ge=1, le=300)

    @field_validator("script")
    @classmethod
    def script_must_not_be_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("script must not be empty")
        if len(value.encode("utf-8")) > 256 * 1024:
            raise ValueError("script exceeds the 256 KiB limit")
        return value


class ExecutionAccepted(BaseModel):
    execution_id: UUID
    status: ExecutionStatus
    status_url: str


class ExecutionResult(BaseModel):
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    duration_ms: int | None = Field(default=None, ge=0)
    executed_sha256: str | None = None


class ExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_id: UUID
    status: ExecutionStatus
    script: str
    script_sha256: str
    timeout_seconds: int
    delivery_deadline: datetime
    created_at: datetime
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    terminal_reason: str | None = None
    result: ExecutionResult | None = None


class DeviceRead(BaseModel):
    id: UUID
    hostname: str
    fingerprint: str
    agent_version: str | None
    online: bool
    revoked: bool
    last_seen_at: datetime | None = None
    created_at: datetime


class InventoryWrite(BaseModel):
    collected_at: datetime
    os: dict[str, Any]
    hardware: dict[str, Any]
    disks: list[dict[str, Any]] = Field(default_factory=list, max_length=128)
    installed_software: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)
    last_boot_at: datetime | None = None


class InventoryRead(InventoryWrite):
    device_id: UUID
    received_at: datetime


class EventLogQueryCreate(BaseModel):
    logs: list[Literal["Application", "System", "Setup"]] = Field(
        default_factory=lambda: ["System", "Application"], min_length=1, max_length=3
    )
    severities: list[Literal["critical", "error", "warning", "information"]] = Field(
        default_factory=lambda: ["critical", "error", "warning"],
        min_length=1,
        max_length=4,
    )
    provider_names: list[str] = Field(default_factory=list, max_length=20)
    event_ids: list[int] = Field(default_factory=list, max_length=50)
    start_time: datetime | None = None
    end_time: datetime | None = None
    max_events: int = Field(default=100, ge=1, le=200)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    delivery_timeout_seconds: int = Field(default=15, ge=1, le=300)

    @field_validator("provider_names")
    @classmethod
    def validate_provider_names(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 128 for value in cleaned):
            raise ValueError("provider names must contain 1 to 128 characters")
        return list(dict.fromkeys(cleaned))

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, values: list[int]) -> list[int]:
        if any(value < 0 or value > 65535 for value in values):
            raise ValueError("event IDs must be between 0 and 65535")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_time_range(self) -> "EventLogQueryCreate":
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("start_time and end_time must be supplied together")
        for value in (self.start_time, self.end_time):
            if value is not None and value.tzinfo is None:
                raise ValueError("event-log timestamps must include a UTC offset")
        if self.start_time and self.end_time:
            if self.start_time >= self.end_time:
                raise ValueError("start_time must be earlier than end_time")
            if self.end_time - self.start_time > timedelta(days=7):
                raise ValueError("event-log time range cannot exceed seven days")
        return self


class EventLogQueryAccepted(BaseModel):
    query_id: UUID
    status: ExecutionStatus
    status_url: str


class EventLogEntry(BaseModel):
    record_id: int | None = None
    log_name: str
    provider_name: str
    event_id: int
    level: int | None = None
    level_name: str | None = None
    time_created: datetime
    machine_name: str | None = None
    message: str | None = None


class EventLogQueryRead(BaseModel):
    query_id: UUID
    device_id: UUID
    status: ExecutionStatus
    filters: EventLogQueryCreate
    events: list[EventLogEntry] | None = None
    output_truncated: bool = False
    duration_ms: int | None = None
    terminal_reason: str | None = None
    parse_error: str | None = None


class AuditEventRead(BaseModel):
    id: UUID
    event_type: str
    actor_id: str
    device_id: UUID | None
    execution_id: UUID | None
    occurred_at: datetime
    details: dict[str, Any]
