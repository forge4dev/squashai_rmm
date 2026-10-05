# Squash RMM

AI-oriented remote monitoring and management composed of a Python control plane,
a native C# Windows agent, the `SquashAgent.msi` unattended installer, and a
minimal OpenAI diagnostic driver in `ai-driver`.

The implementation is built as a vertical slice in this order:

1. Control-plane API contracts, authentication, persistence, and job lifecycle.
2. Authenticated C# agent WebSocket and bounded PowerShell execution.
3. Windows Service hosting, DPAPI-protected identity, inventory, and a local
   encrypted idempotency journal.
4. WiX MSI installation, enrollment, upgrade, and removal.
5. API-ready device inventory and bounded, structured Windows event-log queries.

To rebuild the validated MSI, install the .NET 8 SDK and run from an elevated
PowerShell window:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\installer\build-msi.ps1 -DotNet dotnet.exe
```

The package is written to `artifacts\msi\SquashAgent.msi`. See
`installer\README.md` for secure unattended enrollment and uninstall commands.

The production deployment target is a native Windows VM. Docker is not used.

## Submission documents

- [Setup instructions](docs/SETUP_INSTRUCTIONS.md)
- [Design notes](docs/CONTROL_PLANE_DESIGN.md)
- [Threat model](docs/THREAT_MODEL.md)
- [How it was built](docs/HOW_IT_WAS_BUILT.md)

