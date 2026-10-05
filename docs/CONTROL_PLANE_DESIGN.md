# Design notes

Squash RMM is deliberately smaller than a commercial RMM. It provides the
execution channel an AI operator needs: authenticated device enrollment, live
reachability, raw PowerShell dispatch, unambiguous execution handles, bounded
structured results, and a durable audit record. It does not implement chat,
approvals, patch management, antivirus, or remote desktop.

## Connection model

The Windows service initiates a persistent outbound WebSocket over HTTPS, so an
endpoint behind NAT needs no inbound rule or public address. A random per-device
bearer credential authenticates the connection; only its SHA-256 digest is
stored by the control plane. The C# service stores the plaintext credential with
machine-scoped Windows DPAPI. TLS authenticates the server and protects the
credential, scripts, and results in transit.

Reachability is live connection state, not a guess derived from an old timestamp.
`last_seen_at` remains available as separate diagnostic data. A newer connection
replaces an older connection for the same device. SQLite is authoritative for
the single-instance assessment deployment; the in-memory connection map can be
rebuilt as agents reconnect after a restart.

A persistent channel was chosen over polling because dependent diagnostic steps
must complete in roughly two seconds. When a caller commits an execution, the
control plane pushes it immediately to the connected agent. The caller polls a
stable execution URL at 200 ms intervals, which is inexpensive for this
single-client assessment and keeps the API conventional. A separate interactive
shell session was not added: fast individual executions preserve per-step
timeouts, idempotency, exact-script audit records, and independent terminal
outcomes.

## Device identity and enrollment

An authenticated API caller creates a cryptographically random enrollment token
bound to the endpoint's precomputed fingerprint. The fingerprint is a
domain-separated SHA-256 hash of Windows `MachineGuid`. The database stores only
the token hash. Tokens expire within 15 minutes, are consumed atomically, and
cannot be reused. Enrollment exchanges that temporary capability for an
unrelated device credential.

The MSI accepts an ACL-protected token file rather than a secret command-line
property, performs enrollment as SYSTEM, and deletes the token after success.
The standing API key is kept on the trusted MSP side. Re-enrollment finds the
device by fingerprint, retains its stable UUID and history, revokes earlier
credentials, and issues a new one. A normal uninstall retains DPAPI-protected
identity under ProgramData, so an upgrade or reinstall continues as the same
device without another enrollment exchange.

The fingerprint is an association hint, not an authenticator. Authentication is
always possession of the random device credential. Revocation invalidates every
credential for that device and closes the active connection.

## Job lifecycle

The lifecycle is:

```text
queued -> dispatched -> running -> succeeded | failed | timed_out | agent_interrupted
queued/dispatched -> delivery_expired
```

An execution is committed before HTTP `202` is returned. Caller identity plus
`Idempotency-Key` is unique. Repeating an identical request returns the existing
execution; reusing the key for different content returns `409`.

Online devices receive jobs immediately. Pending jobs are replayed after
reconnection. The agent's DPAPI-encrypted endpoint journal makes repeated
delivery safe: an execution ID already in the journal returns its recorded result
instead of starting a second PowerShell process. Scripts are hashed over exact
UTF-8 content; the result includes the hash of what the agent executed. A
mismatch produces a failed terminal record and security audit event.

The agent launches a fresh noninteractive PowerShell child process for each job,
captures stdout and stderr separately, enforces the runtime deadline, and kills
the process tree on timeout. No AI logic lives on the endpoint. The control-plane
worker independently expires undelivered or abandoned work, so a crashed or
partitioned endpoint cannot leave a job nonterminal forever.

Output is limited to 1 MiB for each stream and truncation is explicit. Script,
stdout, and stderr fields are encrypted at rest with Fernet; application logs
contain identifiers and metadata only.

## API and AI-driver boundary

The API accepts raw script content and immediately returns `202` plus a unique
execution ID. `GET /v1/executions/{id}` returns status and, when terminal, exit
code, stdout, stderr, duration, truncation flags, terminal reason, and the
agent-reported hash. The exact script remains attached to the execution audit
record. Caller authentication, device authentication, and idempotency are
separate concerns and use separate credentials/keys.

The optional AI driver is a consumer of this API, not part of the trusted
execution core. It fixes the target device before the model starts, exposes one
bounded sequential execution tool, treats endpoint text as untrusted evidence,
and verifies script hashes before returning results to the model. Its assessment
policy permits read-only diagnosis only. Approval and remediation policy remain
the responsibility of the calling application, as required by the brief.

## Extra-credit endpoint data

The agent uploads an inventory snapshot at startup and every six hours. The API
can therefore return OS/build, hardware, disks, installed software, and last boot
without dispatching a new job. Event-log access is modeled as a specialized API
over the same execution lifecycle: validated filters produce a fixed
server-generated PowerShell script, and the result is parsed into typed events.
This reuses authentication, idempotency, timeout, offline-expiry, output-limit,
hash-verification, and audit behavior rather than creating a privileged side
channel.

## What I would build next

The assessment deployment is intentionally one control-plane process using a
local SQLite database in WAL mode. The first production step would be code-signing
the MSI and executable, adding automatic certificate rotation, and placing API
keys and field-encryption keys in a managed secret store or HSM. Horizontal
scaling would migrate persistence to HA PostgreSQL and replace the in-memory
connection map with connection-owner leases plus Redis or NATS routing.

Operational work would add signed auto-update with rollback, OpenTelemetry
metrics and traces, external SIEM audit export, tested restore drills, per-tenant
and per-device rate limits, and stale-agent alerts. Security work would add
hardware-backed device identity/attestation, constrained execution identities,
script-policy and approval hooks, stronger prompt-injection isolation for AI
consumers, and organization/tenant boundaries. Product extensions would include
bounded file transfer and reboot tracking while continuing to reuse the same job
and audit primitives.

