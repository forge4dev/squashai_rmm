[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $SourceRoot,
    [Parameter(Mandatory)] [SecureString] $ApiKey,
    [Parameter(Mandatory)] [string] $PublicBaseUrl,
    [Parameter(Mandatory)] [string] $TlsCertificateFile,
    [Parameter(Mandatory)] [string] $TlsPrivateKeyFile,
    [string] $PythonExe = "python.exe",
    [string] $InstallRoot = "C:\Program Files\SquashRMM\ControlPlane",
    [string] $DataRoot = "C:\ProgramData\SquashRMM\ControlPlane",
    [SecureString] $DatabaseUrl,
    [string] $DatabasePath,
    [int] $Port = 8443
)

$ErrorActionPreference = "Stop"

function ConvertFrom-SecureValue([SecureString] $Value) {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Value)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer from an elevated PowerShell session."
}
if (-not $PublicBaseUrl.StartsWith("https://", [StringComparison]::OrdinalIgnoreCase)) {
    throw "PublicBaseUrl must use HTTPS."
}
$resolvedSource = (Resolve-Path -LiteralPath $SourceRoot).Path
$resolvedCert = (Resolve-Path -LiteralPath $TlsCertificateFile).Path
$resolvedKey = (Resolve-Path -LiteralPath $TlsPrivateKeyFile).Path
if (-not (Test-Path -LiteralPath (Join-Path $resolvedSource "pyproject.toml"))) {
    throw "SourceRoot must point to the control-plane directory."
}

New-Item -ItemType Directory -Force -Path $InstallRoot, $DataRoot | Out-Null
& icacls.exe $DataRoot /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
foreach ($existingProtectedFile in @("server-cert.pem", "server-key.pem", "control-plane.env")) {
    $existingProtectedPath = Join-Path $DataRoot $existingProtectedFile
    if (Test-Path -LiteralPath $existingProtectedPath) {
        & icacls.exe $existingProtectedPath /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F" | Out-Null
    }
}
$service = Get-Service -Name "SquashRMMControlPlane" -ErrorAction SilentlyContinue
if ($service) {
    Stop-Service -Name "SquashRMMControlPlane" -Force -ErrorAction SilentlyContinue
    Set-Service -Name "SquashRMMControlPlane" -StartupType Disabled
}
$taskName = "SquashRMMControlPlaneStartup"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}

Get-ChildItem -LiteralPath $resolvedSource -Force |
    Where-Object { $_.Name -notin @(".venv", ".pytest_cache", "squash-dev.db") } |
    Copy-Item -Destination $InstallRoot -Recurse -Force

$certificateTarget = Join-Path $DataRoot "server-cert.pem"
$keyTarget = Join-Path $DataRoot "server-key.pem"
Copy-Item -LiteralPath $resolvedCert -Destination $certificateTarget -Force
Copy-Item -LiteralPath $resolvedKey -Destination $keyTarget -Force

$venv = Join-Path $InstallRoot ".venv"
if (-not (Test-Path -LiteralPath $venv)) { & $PythonExe -m venv $venv }
$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $InstallRoot "requirements.lock")
& $venvPython -m pip install --disable-pip-version-check --no-deps $InstallRoot
& (Join-Path $venv "Scripts\pywin32_postinstall.py") -install

if ($DatabaseUrl) {
    $databasePlaintext = ConvertFrom-SecureValue $DatabaseUrl
} else {
    if (-not $DatabasePath) { $DatabasePath = Join-Path $DataRoot "squash-rmm.db" }
    $fullDatabasePath = [IO.Path]::GetFullPath($DatabasePath)
    $fullDataRoot = [IO.Path]::GetFullPath($DataRoot).TrimEnd('\')
    if (-not $fullDatabasePath.StartsWith($fullDataRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "The SQLite database must be stored under DataRoot."
    }
    $databasePlaintext = "sqlite+pysqlite:///" + $fullDatabasePath.Replace('\', '/')
}
$apiKeyPlaintext = ConvertFrom-SecureValue $ApiKey
try {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hashBytes = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($apiKeyPlaintext))
        $apiKeyHash = -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
    } finally { $sha.Dispose() }
    $configPath = Join-Path $DataRoot "control-plane.env"
    $existingKeyLine = $null
    if (Test-Path -LiteralPath $configPath) {
        $existingKeyLine = Get-Content -LiteralPath $configPath |
            Where-Object { $_.StartsWith("SQUASH_DATA_ENCRYPTION_KEY=") } |
            Select-Object -First 1
    }
    if ($existingKeyLine) {
        $dataEncryptionKey = $existingKeyLine.Substring("SQUASH_DATA_ENCRYPTION_KEY=".Length)
    } else {
        $encryptionBytes = New-Object byte[] 32
        $random = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $random.GetBytes($encryptionBytes) }
        finally { $random.Dispose() }
        $dataEncryptionKey = [Convert]::ToBase64String($encryptionBytes)
    }

    @(
        "SQUASH_ENVIRONMENT=production"
        "SQUASH_DATABASE_URL=$databasePlaintext"
        "SQUASH_API_KEY_SHA256=$apiKeyHash"
        "SQUASH_PUBLIC_BASE_URL=$($PublicBaseUrl.TrimEnd('/'))"
        "SQUASH_LISTEN_HOST=0.0.0.0"
        "SQUASH_LISTEN_PORT=$Port"
        "SQUASH_TLS_CERTIFICATE_FILE=$certificateTarget"
        "SQUASH_TLS_PRIVATE_KEY_FILE=$keyTarget"
        "SQUASH_DATA_ENCRYPTION_KEY=$dataEncryptionKey"
    ) | Set-Content -LiteralPath $configPath -Encoding UTF8
} finally {
    $databasePlaintext = $null
    $apiKeyPlaintext = $null
}

foreach ($protectedPath in @($certificateTarget, $keyTarget, (Join-Path $DataRoot "control-plane.env"))) {
    & icacls.exe $protectedPath /inheritance:r /grant:r "*S-1-5-18:F" "*S-1-5-32-544:F" | Out-Null
}

Push-Location $InstallRoot
try { & $venvPython -m alembic upgrade head }
finally { Pop-Location }

$runner = Join-Path $venv "Scripts\squash-control-plane.exe"
$taskAction = New-ScheduledTaskAction -Execute $runner -WorkingDirectory $DataRoot
$taskTrigger = New-ScheduledTaskTrigger -AtStartup
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest
$taskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
Register-ScheduledTask `
    -TaskName $taskName `
    -Action $taskAction `
    -Trigger $taskTrigger `
    -Principal $taskPrincipal `
    -Settings $taskSettings `
    -Description "Runs the Squash RMM control plane before user logon." `
    -Force | Out-Null
Start-ScheduledTask -TaskName $taskName

$firewallName = "Squash RMM Control Plane HTTPS"
if (-not (Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $firewallName -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port | Out-Null
}

Write-Host "Squash RMM Control Plane startup task launched for $PublicBaseUrl"
