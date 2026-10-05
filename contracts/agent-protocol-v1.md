# Agent protocol v1

All messages are UTF-8 JSON objects containing `protocolVersion: 1`. The agent
connects outbound over a mutually authenticated WebSocket. Message bodies and
endpoint output are untrusted data and are never interpreted as control-plane
instructions.

## Server to agent: execute

```json
{
  "protocolVersion": 1,
  "type": "execute",
  "executionId": "00000000-0000-0000-0000-000000000000",
  "script": "Get-Date",
  "scriptSha256": "lowercase hexadecimal SHA-256",
  "timeoutSeconds": 60,
  "outputLimitBytesPerStream": 1048576
}
```

## Agent to server: state

```json
{
  "protocolVersion": 1,
  "type": "state",
  "executionId": "00000000-0000-0000-0000-000000000000",
  "status": "running",
  "occurredAt": "2026-01-01T00:00:00Z"
}
```

## Agent to server: result

```json
{
  "protocolVersion": 1,
  "type": "result",
  "executionId": "00000000-0000-0000-0000-000000000000",
  "status": "succeeded",
  "exitCode": 0,
  "stdout": "output",
  "stderr": "",
  "stdoutTruncated": false,
  "stderrTruncated": false,
  "durationMs": 250,
  "executedSha256": "lowercase hexadecimal SHA-256"
}
```

An execution ID is durable and idempotent. An agent that has already accepted
an ID must return its saved state or result and must not start a second process.

