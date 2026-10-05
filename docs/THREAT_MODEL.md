# Threat model

## Scope and security objectives

This model covers the Python control plane, SQLite database, HTTPS/WebSocket
transport, C# Windows service, MSI enrollment flow, PowerShell child processes,
inventory/event-log APIs, and demonstration AI driver. The primary objectives
are that only authenticated callers can request execution, only the intended
device can accept a job, executed bytes match the audited script, every job
terminates, and endpoint-originated content remains data rather than control.

The trusted operator/API caller is intentionally allowed to submit arbitrary
PowerShell. Approval decisions and AI policy are outside this execution layer.

## Trust boundaries, assets, and attackers

Trust boundaries exist between the Squash API caller, public HTTPS service,
SQLite persistence, endpoint service, PowerShell child process, Windows host,
and external LLM API. Important assets are API keys, enrollment tokens, device
credentials, the TLS and data-encryption private keys, raw scripts, endpoint
output, device identity, and durable execution/audit history.

Relevant attackers include an unauthenticated network client, a passive or active
network adversary, someone who captures installer material, a compromised
endpoint running without administrative control, malicious endpoint output, and
an authenticated but buggy caller that retries or loses responses. A local
Administrator/SYSTEM compromise and a malicious authorized RMM operator are
treated separately under deferred risks.

## Defended threats

- Unauthenticated callers cannot create enrollment tokens, list devices,
  dispatch scripts, inspect results, or revoke devices.
- Caller API keys and device credentials are independently generated and stored
  only as SHA-256 digests by the control plane. Devices are individually
  revocable; revocation also closes the live channel.
- Installer enrollment tokens are random, short-lived, single-use, stored only
  as hashes, and bound to a specific fingerprint. They never become standing
  device credentials.
- The MSI accepts an ACL-protected enrollment-token file rather than a plaintext
  token property. Its deferred enrollment action runs as SYSTEM, hides its target
  from MSI logs, checks the agent exit code, and deletes the file only after a
  successful exchange.
- Device credentials and the local execution journal are encrypted with
  machine-scoped DPAPI. MSI-created state directories grant access only to
  SYSTEM and local Administrators.
- HTTPS protects credentials and payloads in transit. Production mode refuses
  HTTP. The agent initiates all connectivity, so no endpoint listener or inbound
  firewall opening is introduced.
- Unique idempotency constraints prevent a retried caller request from creating a
  second execution. Endpoint execution IDs provide a second idempotency layer.
- Exact script hashes are sent and verified with results. A reported mismatch is
  a terminal failure and a security audit event.
- Each execution is persisted before `202` is returned and is tied to its caller,
  target, exact script, timestamps, and result. This is a durable audit record,
  although it is not a cryptographically immutable external ledger.
- Agent messages are schema-validated and may update only executions belonging to
  that authenticated device. Invalid transitions close the connection.
- Endpoint text is opaque data. The console inserts it with `textContent`, its
  Content Security Policy blocks inline code, and the server never evaluates
  output.
- Inventory and event fields remain untrusted data. Event queries use fixed
  server-generated PowerShell with data-only filters; log names, range, result
  count, providers, IDs, message length, runtime, and total output are bounded.
  Security-log access is deliberately excluded from this assessment build.
- Request, script, and output sizes are bounded. Workers force offline or hung
  jobs into terminal states.
- Script and output payloads are encrypted at rest. Tokens, authorization
  headers, scripts, and output are excluded from application access logs.

## Abuse cases and controls

**Captured installer material.** An enrollment token is useful only for its
bound fingerprint, expires within 15 minutes, and is atomically single-use. It
does not authorize API calls or become the device credential. The MSI receives a
protected file path, not the token value in a logged command line.

**Dispatch retry or duplicate delivery.** The caller identity and idempotency
key uniquely identify a request; changing content under the same key returns
`409`. The endpoint journal is keyed by execution ID, so a replayed WebSocket
message returns the earlier outcome without rerunning code.

**Offline, hung, or disconnected endpoint.** Delivery and execution deadlines
are independent. The agent kills a timed-out PowerShell process tree, while the
server expires work even if the endpoint disappears. All transitions are
validated and terminal outcomes are retained.

**Malicious endpoint data.** The control plane does not execute or template
stdout, stderr, inventory, event messages, or hostnames. The browser console
uses `textContent` and a Content Security Policy. The AI driver labels endpoint
evidence untrusted and gives the model only one bounded tool; this reduces but
does not eliminate model prompt-injection risk.

**Database or backup disclosure.** Sensitive execution columns are encrypted
with a separate Fernet key. The SQLite database, environment file, TLS key, and
backups are restricted to SYSTEM and Administrators. Application logs contain
identifiers and typed error categories rather than credentials or payloads.

**Event-log query injection.** The event API validates an allow-list of logs,
bounded severities, provider names, IDs, range, and count. Values are encoded as
data for a fixed server-generated script rather than interpolated as executable
PowerShell. Security-log access is excluded from this assessment build.

## Deferred risks

A local Administrator or fully compromised endpoint can impersonate that
endpoint, tamper with the agent, and extract secrets available to SYSTEM. Windows
`MachineGuid` is not hardware attestation and cloned, non-generalized VMs can
share it. Production device identity should use TPM-backed keys and attestation.

An authorized caller can intentionally run destructive administrative
PowerShell. This is by design for the execution layer; production deployment
requires the calling application to enforce approvals, tenant authorization,
script policy, and least-privilege execution. The current service runs as
LocalSystem, so endpoint impact is correspondingly high.

General detection of secrets inside arbitrary scripts or output is unreliable.
Encryption and log redaction reduce exposure but do not make collecting secrets
safe; operators and AI policy must minimize collected data. When the AI driver is
used, selected endpoint evidence is sent to the configured external LLM API.

The assessment MSI and executable are unsigned. Production distribution requires
organization-controlled Authenticode signing, timestamping, a protected build
pipeline, and signed update/rollback. Other deferred controls include managed HSM
integration, multi-tenant authorization, Internet-scale denial-of-service
protection, HA/multi-region operation, external append-only SIEM export,
automated certificate/key rotation, and independent penetration testing.

