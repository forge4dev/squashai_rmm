# SquashAgent.msi

The MSI installs a self-contained x64 agent under
`C:\Program Files\SquashRMM\Agent`, enrolls it, and registers the automatic
`SquashAgent` Windows Service running as LocalSystem. Persistent identity and
the encrypted execution journal remain under
`C:\ProgramData\SquashRMM\Agent` across uninstall/reinstall.

## Build

```powershell
# Run this from an elevated PowerShell window with the .NET 8 SDK installed so
# WiX ICE validation can access the Windows Installer service.
.\installer\build-msi.ps1 -DotNet "dotnet.exe"
```

Output: `artifacts\msi\SquashAgent.msi`.

## Silent install and enrollment

Create the one-time, fingerprint-bound enrollment token through the control
plane, and place only the token in a file readable by SYSTEM and Administrators.
Do not put the token itself on the `msiexec` command line.

MSP deployment tooling can calculate the endpoint fingerprint before installing
the agent with the bundled helper. It implements the same domain-separated
MachineGuid hash as the agent:

```powershell
$fingerprint = & .\get-device-fingerprint.ps1
```

```powershell
$tokenFile = "C:\ProgramData\SquashRMM\enrollment-token.txt"
icacls.exe $tokenFile /inheritance:r /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F"

msiexec.exe /i .\SquashAgent.msi /qn /norestart `
  CONTROL_PLANE_URL="https://rmm.demo.local:8443" `
  ENROLLMENT_TOKEN_FILE="$tokenFile" `
  /l*v "$env:TEMP\SquashAgent-install.log"
```

The MSI hides the enrollment custom-action target. The token value never enters
an MSI property or command line, and the agent deletes the token file only after
successful enrollment. A failed enrollment fails and rolls back the install.

Verify:

```powershell
Get-Service SquashAgent
Test-Path "C:\ProgramData\SquashRMM\Agent\agent.json"
```

## Upgrade and uninstall

An upgrade preserves existing DPAPI-protected identity and does not require an
enrollment token:

```powershell
msiexec.exe /i .\SquashAgent.msi /qn /norestart
```

Uninstall the service and binaries while retaining enrollment state:

```powershell
msiexec.exe /x .\SquashAgent.msi /qn /norestart
```

Retaining `C:\ProgramData\SquashRMM\Agent` allows a reinstall to preserve the
device credential. Delete that directory separately only when intentionally
decommissioning the device.
