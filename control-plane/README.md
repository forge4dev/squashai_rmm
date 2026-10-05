# Squash RMM Control Plane

Python/FastAPI control plane for enrolled Squash Windows agents. The assessment
deployment runs natively under a pre-logon SYSTEM startup task with SQLite and
HTTPS; Docker is not used. PostgreSQL remains an optional future backend.

## Local development

Requirements: Python 3.12 and PowerShell.

```powershell
cd control-plane
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
..\deploy\windows\start-development.ps1 -ApiKey "development-only-key"
```

Open:

- Swagger: `http://127.0.0.1:8000/docs`
- Execution console: `http://127.0.0.1:8000/console`
- Readiness: `http://127.0.0.1:8000/ready`

Development uses SQLite and may store sensitive fields as plaintext. Production
requires a persistent database, HTTPS, API authentication, and a field-encryption
key.

## Native Windows VM deployment

Prerequisites:

1. Windows Server or Windows 10/11 x64 with an elevated PowerShell session.
2. Python 3.12 x64 available as `python.exe`.
3. A DNS name resolving to the VM.
4. PEM TLS certificate and private-key files for that DNS name.

For an isolated demo, a 30-day self-signed certificate can be generated after
installing the Python dependencies. Trust the resulting certificate explicitly
on the control-plane and endpoint VMs; do not use it for production:

```powershell
.\.venv\Scripts\python.exe .\tools\generate_dev_certificate.py rmm.demo.local C:\secure\squash-tls
```

```powershell
$apiKey = Read-Host "Initial Squash API key" -AsSecureString

.\deploy\windows\install-control-plane.ps1 `
  -SourceRoot "$PWD\control-plane" `
  -ApiKey $apiKey `
  -PublicBaseUrl "https://rmm.demo.local:8443" `
  -TlsCertificateFile "C:\secure\squash-tls\server-cert.pem" `
  -TlsPrivateKeyFile "C:\secure\squash-tls\server-key.pem"
```

The script creates `C:\ProgramData\SquashRMM\ControlPlane\squash-rmm.db`, applies
Alembic migrations, protects the database and configuration with
SYSTEM/Administrators-only ACLs, registers and starts the
`SquashRMMControlPlaneStartup` SYSTEM task, and opens the configured firewall
port. The task starts before user logon and restarts the process after failures.
Application files live under
`C:\Program Files\SquashRMM\ControlPlane`; configuration and TLS material live
under `C:\ProgramData\SquashRMM\ControlPlane`.

To remove application files while retaining the database and configuration:

```powershell
.\deploy\windows\uninstall-control-plane.ps1
```

Create a consistent database and encryption-key backup:

```powershell
.\deploy\windows\backup-control-plane.ps1 -DestinationDirectory "D:\SquashRMM-Backups"
```

Add `-RemoveData` only when the local configuration, SQLite database, and TLS
private key should also be permanently removed.

## Primary API flow

1. An authenticated caller creates an enrollment token bound to the endpoint's
   SHA-256 device fingerprint.
2. The installer gives that single-use token to the agent.
3. The agent exchanges it for a separate 256-bit device credential.
4. The agent stores the credential with Windows DPAPI and opens the outbound
   authenticated WebSocket.
5. A caller posts raw PowerShell with an `Idempotency-Key` and receives an
   execution ID immediately.
6. The caller retrieves the structured result by execution ID.

## Extra-credit endpoint data

- `GET /v1/devices/{device_id}/inventory` returns the agent's latest OS,
  hardware, disk, installed-software, and boot-time snapshot without running a
  new script. The agent uploads immediately on service start and every six hours.
- `POST /v1/devices/{device_id}/event-log-queries` accepts validated log,
  severity, time, provider, and event-ID filters and immediately returns a query
  ID. Poll `GET /v1/event-log-queries/{query_id}` for typed event records.

Event queries use the normal authenticated, idempotent execution channel, so
offline expiry, execution timeout, output limits, exact-script audit storage,
and script-hash verification all still apply. Queries are limited to 200 events
over at most seven days and to the Application, System, and Setup logs.

See [`demo_requests.http`](demo_requests.http) for request examples. The generated
OpenAPI document at `/docs` is the authoritative field reference.

## Tests

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests migrations
.\.venv\Scripts\python.exe -m pytest -q
```

SQLite runs in WAL mode with foreign keys enabled and a 30-second busy timeout.
Run exactly one control-plane process; the SQLite file must remain on the
VM's local disk, not a network share. Install the optional `postgres` dependency
group when moving to a multi-instance PostgreSQL deployment.

The test suite covers API authentication, enrollment binding and replay,
credential rotation, idempotent dispatch, live WebSocket execution, inventory,
structured event queries, revocation, script-hash reporting, and encryption at
rest.
