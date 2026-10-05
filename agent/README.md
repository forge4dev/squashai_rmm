# Squash Windows Agent

The agent is a deterministic .NET 8 Windows Service. It initiates an outbound
authenticated WebSocket, executes the exact PowerShell text dispatched by the
control plane, and returns bounded structured results. It contains no AI logic.

## Build

Install the .NET 8 SDK, then run:

```powershell
dotnet restore .\agent\Squash.Agent.sln
dotnet build .\agent\Squash.Agent.sln -c Release --no-restore
```

## Enrollment and console-mode smoke test

The fingerprint is derived from Windows MachineGuid with a domain-separated
SHA-256 hash. It survives an agent reinstall without exposing MachineGuid.

```powershell
$agent = ".\agent\src\Squash.Agent\bin\Release\net8.0-windows\win-x64\Squash.Agent.exe"
$fingerprint = & $agent fingerprint
```

Create a fingerprint-bound, single-use enrollment token through the control
plane. Put only that token in an ACL-protected temporary file, then enroll:

```powershell
& $agent enroll --server "https://rmm.demo.local:8443" `
  --token-file "C:\secure\enrollment-token.txt" `
  --delete-token-file
& $agent run
```

The long-lived device credential is never accepted on the command line. It is
stored under `C:\ProgramData\SquashRMM\Agent\agent.json`, encrypted with
machine-scoped Windows DPAPI. The execution journal is also DPAPI-encrypted.
The MSI will apply SYSTEM/Administrators-only ACLs and register the executable
as the automatic `SquashAgent` Windows Service.

## Execution guarantees

- Verifies the UTF-8 SHA-256 of every script before starting PowerShell.
- Writes a durable `running` record before process creation, preventing a
  retried execution ID from starting twice.
- Runs one job at a time with `-NoProfile` and `-NonInteractive`.
- Continuously drains stdout and stderr while retaining at most the server's
  bounded limit (with a hard 1 MiB limit per stream).
- Kills the complete PowerShell process tree on timeout or service shutdown.
- Reports an interrupted durable job after restart rather than rerunning it.
- Uses OS certificate validation; there is no TLS bypass option.
