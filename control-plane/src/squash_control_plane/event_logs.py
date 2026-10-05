import base64
import json
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status

from .auth import Caller
from .database import SessionDependency
from .executions import create_execution, execution_to_read
from .models import Execution
from .schemas import (
    EventLogEntry,
    EventLogQueryAccepted,
    EventLogQueryCreate,
    EventLogQueryRead,
    ExecutionCreate,
    ExecutionStatus,
)

router = APIRouter(prefix="/v1", tags=["event-logs"])

_MARKER = "# squash:event-log-query:v1:"
_LEVELS = {"critical": 1, "error": 2, "warning": 3, "information": 4}


def _metadata(filters: EventLogQueryCreate) -> dict[str, Any]:
    return filters.model_dump(mode="json")


def build_event_log_script(filters: EventLogQueryCreate) -> str:
    metadata_json = json.dumps(
        _metadata(filters), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    encoded = base64.b64encode(metadata_json.encode("utf-8")).decode("ascii")
    level_values = [_LEVELS[value] for value in filters.severities]
    return f"""{_MARKER}{encoded}
$ErrorActionPreference = 'Stop'
$specJson = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))
$spec = $specJson | ConvertFrom-Json
$endTime = if ($null -eq $spec.end_time) {{ [DateTime]::UtcNow }} else {{
    [DateTimeOffset]::Parse($spec.end_time).UtcDateTime
}}
$startTime = if ($null -eq $spec.start_time) {{ $endTime.AddHours(-1) }} else {{
    [DateTimeOffset]::Parse($spec.start_time).UtcDateTime
}}
$filter = @{{
    LogName = @($spec.logs)
    StartTime = $startTime
    EndTime = $endTime
    Level = @({",".join(str(value) for value in level_values)})
}}
if (@($spec.provider_names).Count -gt 0) {{ $filter.ProviderName = @($spec.provider_names) }}
if (@($spec.event_ids).Count -gt 0) {{ $filter.Id = @($spec.event_ids) }}
$records = @(Get-WinEvent -FilterHashtable $filter -MaxEvents ([int]$spec.max_events) |
    ForEach-Object {{
        $message = $null
        try {{ $message = $_.FormatDescription() }} catch {{ $message = $null }}
        if ($null -ne $message -and $message.Length -gt 8192) {{
            $message = $message.Substring(0, 8192)
        }}
        [ordered]@{{
            record_id = $_.RecordId
            log_name = $_.LogName
            provider_name = $_.ProviderName
            event_id = $_.Id
            level = $_.Level
            level_name = $_.LevelDisplayName
            time_created = $_.TimeCreated.ToUniversalTime().ToString('o')
            machine_name = $_.MachineName
            message = $message
        }}
    }})
ConvertTo-Json -InputObject $records -Depth 4 -Compress
"""


def _filters_from_script(script: str) -> EventLogQueryCreate | None:
    first_line = script.splitlines()[0] if script else ""
    if not first_line.startswith(_MARKER):
        return None
    try:
        decoded = base64.b64decode(first_line[len(_MARKER) :], validate=True)
        return EventLogQueryCreate.model_validate_json(decoded)
    except (ValueError, json.JSONDecodeError):
        return None


def _parse_events(execution: Execution) -> tuple[list[EventLogEntry] | None, str | None]:
    if execution.status != ExecutionStatus.SUCCEEDED.value:
        return None, None
    if execution.stdout_truncated:
        return None, "Event output exceeded the configured limit"
    try:
        raw = json.loads(execution.stdout or "[]")
        if not isinstance(raw, list):
            raw = [raw]
        return [EventLogEntry.model_validate(item) for item in raw], None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None, "Agent returned invalid structured event data"


@router.post(
    "/devices/{device_id}/event-log-queries",
    response_model=EventLogQueryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_event_log_query(
    device_id: UUID,
    body: EventLogQueryCreate,
    caller: Caller,
    session: SessionDependency,
    request: Request,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=118)],
) -> EventLogQueryAccepted:
    filters = body
    accepted = await create_execution(
        device_id,
        ExecutionCreate(
            script=build_event_log_script(filters),
            timeout_seconds=filters.timeout_seconds,
            delivery_timeout_seconds=filters.delivery_timeout_seconds,
        ),
        caller,
        session,
        request,
        f"event-log:{idempotency_key}",
    )
    return EventLogQueryAccepted(
        query_id=accepted.execution_id,
        status=accepted.status,
        status_url=str(request.url_for("get_event_log_query", query_id=accepted.execution_id)),
    )


@router.get(
    "/event-log-queries/{query_id}",
    response_model=EventLogQueryRead,
    name="get_event_log_query",
)
def get_event_log_query(
    query_id: UUID,
    caller: Caller,
    session: SessionDependency,
) -> EventLogQueryRead:
    del caller
    execution = session.get(Execution, query_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Event-log query not found")
    filters = _filters_from_script(execution.script)
    if filters is None:
        raise HTTPException(status_code=404, detail="Event-log query not found")
    read = execution_to_read(execution)
    events, parse_error = _parse_events(execution)
    return EventLogQueryRead(
        query_id=execution.id,
        device_id=execution.device_id,
        status=ExecutionStatus(execution.status),
        filters=filters,
        events=events,
        output_truncated=execution.stdout_truncated,
        duration_ms=execution.duration_ms,
        terminal_reason=read.terminal_reason,
        parse_error=parse_error,
    )
