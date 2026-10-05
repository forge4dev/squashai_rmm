# Setup instructions

This guide takes a fresh source archive to one enrolled Windows endpoint in
under 30 minutes. The assessment deployment uses two Windows machines and no
Docker:

- **Control plane:** Windows Server or Windows 10/11 x64 with Python 3.12 x64.
- **Endpoint:** Windows 10/11 or Windows Server x64.

Run PowerShell as Administrator on both machines. Internet access is needed to
install Python packages if they are not already cached. Commands below assume
the archive is extracted to `D:\SquashRMM`; adjust that path if necessary.

## 1. Prepare the control-plane machine (about 8 minutes)

Open an elevated PowerShell window:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
Set-Location D:\SquashRMM

python.exe -m venv .\control-plane\.venv
.\control-plane\.venv\Scripts\python.exe -m pip install `
  --disable-pip-version-check `
  -r .\control-plane\requirements.lock
```

Choose the address that the endpoint will use for the control-plane machine:

```powershell
$controlPlaneIp = "192.168.1.10"  # replace with this machine's IPv4 address
```

For a lab without DNS, add the demo hostname on both Windows machines:

```powershell
$hostsFile = "$env:SystemRoot\System32\drivers\etc\hosts"
if (-not (Select-String -LiteralPath $hostsFile -SimpleMatch "rmm.demo.local" -Quiet)) {
    Add-Content -LiteralPath $hostsFile -Value "$controlPlaneIp rmm.demo.local"
}
Clear-DnsClientCache
```

Generate a development-only certificate. The generated private key must remain
on the control-plane machine:

```powershell
.\control-plane\.venv\Scripts\python.exe `
  .\control-plane\tools\generate_dev_certificate.py `
  rmm.demo.local `
  C:\secure\squash-tls

certutil.exe -addstore -f Root "C:\secure\squash-tls\server-cert.pem"
```

This self-signed certificate is for the assessment lab only. Production should
use an organization-managed certificate and protected signing/key processes.

## 2. Install the control plane (about 7 minutes)

Choose a strong control-plane API key and keep it in a password manager. The
installer receives it as a `SecureString` and persists only its SHA-256 digest:

```powershell
$apiKey = Read-Host "New control-plane API key" -AsSecureString

.\deploy\windows\install-control-plane.ps1 `
  -SourceRoot "$PWD\control-plane" `
  -ApiKey $apiKey `
  -PublicBaseUrl "https://rmm.demo.local:8443" `
  -TlsCertificateFile "C:\secure\squash-tls\server-cert.pem" `
  -TlsPrivateKeyFile "C:\secure\squash-tls\server-key.pem"
```

The script installs the Python application under `C:\Program
Files\SquashRMM\ControlPlane`, creates the SQLite database under
`C:\ProgramData\SquashRMM\ControlPlane`, applies migrations, restricts file
ACLs, opens TCP 8443, and starts a SYSTEM startup task.

Verify it:

```powershell
Invoke-RestMethod "https://rmm.demo.local:8443/ready"
Get-ScheduledTask -TaskName "SquashRMMControlPlaneStartup"
```

Expected readiness response:

```json
{"status":"ok","service":"squash-control-plane"}
```

## 3. Prepare the clean endpoint (about 3 minutes)

Copy these files to `C:\Demo` on the endpoint:

```text
artifacts\msi\SquashAgent.msi
installer\get-device-fingerprint.ps1
C:\secure\squash-tls\server-cert.pem  (public certificate only)
```

Do not copy `server-key.pem` or the control-plane API key. On the endpoint, add
the same hostname mapping if lab DNS is unavailable, trust the public
certificate, and verify HTTPS:

```powershell
$controlPlaneIp = "192.168.1.10"  # same value used above
$hostsFile = "$env:SystemRoot\System32\drivers\etc\hosts"
if (-not (Select-String -LiteralPath $hostsFile -SimpleMatch "rmm.demo.local" -Quiet)) {
    Add-Content -LiteralPath $hostsFile -Value "$controlPlaneIp rmm.demo.local"
}
Clear-DnsClientCache

certutil.exe -addstore -f Root "C:\Demo\server-cert.pem"
Invoke-RestMethod "https://rmm.demo.local:8443/ready"
```

## 4. Issue a device-bound enrollment token (about 3 minutes)

On the endpoint, calculate the non-secret fingerprint:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
$fingerprint = & "C:\Demo\get-device-fingerprint.ps1"
$fingerprint
```

On the trusted control-plane administrator machine, set `$fingerprint` to that
64-character value. Convert the already-entered secure API key only for the API
request and issue a ten-minute, single-use token:

```powershell
$fingerprint = "PASTE-ENDPOINT-FINGERPRINT-HERE"

function ConvertFrom-SecureValue([Security.SecureString] $Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$apiKeyPlaintext = ConvertFrom-SecureValue $apiKey
try {
    $issued = Invoke-RestMethod `
      -Method Post `
      -Uri "https://rmm.demo.local:8443/v1/enrollment-tokens" `
      -Headers @{"X-API-Key" = $apiKeyPlaintext} `
      -ContentType "application/json" `
      -Body (@{
          expected_fingerprint = $fingerprint
          ttl_seconds = 600
      } | ConvertTo-Json)
} finally {
    $apiKeyPlaintext = $null
}
```

Use the VM platform or MSP deployment channel to deliver only `$issued.token` to
the endpoint. For a local lab, copy it to the clipboard on the trusted
administrator machine without printing it:

```powershell
Set-Clipboard -Value $issued.token
```

On the endpoint, paste it into a masked prompt and write the short-lived secret
to its protected staging location:

```powershell
$tokenDirectory = "C:\ProgramData\SquashRMM"
$tokenFile = Join-Path $tokenDirectory "enrollment-token.txt"
New-Item -ItemType Directory -Path $tokenDirectory -Force | Out-Null

$tokenSecure = Read-Host "Paste one-time enrollment token" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($tokenSecure)
try {
    $tokenPlaintext = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    [IO.File]::WriteAllText(
        $tokenFile,
        $tokenPlaintext,
        [Text.UTF8Encoding]::new($false)
    )
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    $tokenPlaintext = $null
    $tokenSecure.Dispose()
}
Set-Clipboard -Value ""
```

Do not print the token or pass it on the `msiexec` command line. Still on the
endpoint, restrict the staged file:

```powershell
$tokenFile = "C:\ProgramData\SquashRMM\enrollment-token.txt"
icacls.exe $tokenFile /inheritance:r `
  /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F"
```

Token preparation is manual here to make the trust exchange visible. An MSP's
existing deployment tooling normally automates fingerprint collection, token
issuance, secure file delivery, and the following silent command. The endpoint
never receives the standing control-plane API key.

## 5. Install and enroll the endpoint (about 3 minutes)

On the endpoint:

```powershell
$msi = (Resolve-Path "C:\Demo\SquashAgent.msi").Path
$tokenFile = "C:\ProgramData\SquashRMM\enrollment-token.txt"
$logFile = "$env:TEMP\SquashAgent-install.log"
$argumentLine = @(
    "/i `"$msi`""
    "/qn"
    "/norestart"
    "CONTROL_PLANE_URL=https://rmm.demo.local:8443"
    "ENROLLMENT_TOKEN_FILE=`"$tokenFile`""
    "/l*v `"$logFile`""
) -join " "

$installation = Start-Process `
  -FilePath "$env:SystemRoot\System32\msiexec.exe" `
  -ArgumentList $argumentLine `
  -Wait `
  -PassThru

$installation.ExitCode
```

Exit code `0` means installation and enrollment succeeded. The MSI installs a
self-contained agent, enrolls within the installation transaction, deletes the
one-time token after the exchange, and starts an automatic LocalSystem service.

Verify local state:

```powershell
Get-Service SquashAgent
Test-Path "C:\Program Files\SquashRMM\Agent\Squash.Agent.exe"
Test-Path "C:\ProgramData\SquashRMM\Agent\agent.json"
Test-Path $tokenFile  # expected: False
```

## 6. Verify from the control plane (about 1 minute)

Open `https://rmm.demo.local:8443/console`, enter the control-plane API key, and
refresh devices. The endpoint should show **Online** with agent version `0.2.0`.
Swagger/OpenAPI is at `https://rmm.demo.local:8443/docs`.

If installation fails, inspect the MSI log near `Return value 3`:

```powershell
Select-String -Path "$env:TEMP\SquashAgent-install.log" `
  -Pattern "Return value 3" -Context 15,8
```

The most common causes are an expired/used token, a fingerprint mismatch,
untrusted certificate, or incorrect hostname resolution.

## Optional: AI driver UI

On a trusted operator workstation with Python 3.12:

```powershell
Set-Location D:\SquashRMM\ai-driver
python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "."
.\.venv\Scripts\streamlit.exe run streamlit_app.py
```

Open `http://127.0.0.1:8501`. The UI is bound to localhost because it does not
implement its own user authentication. Enter the control-plane and OpenAI API
keys in the password fields; do not commit them to the archive.

