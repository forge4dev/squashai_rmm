[CmdletBinding()]
param(
    [string] $Hostname = "rmm.demo.local",
    [int] $Port = 8443
)

$ErrorActionPreference = "Stop"
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell session."
}
if ($Hostname -notmatch '^[a-zA-Z0-9.-]+$') {
    throw "Hostname contains unsupported characters."
}

$repositoryRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$diagnosticLog = Join-Path $env:TEMP "squash-rmm-local-demo-deploy.log"
Start-Transcript -Path $diagnosticLog -Force | Out-Null
try {
$controlPlaneRoot = Join-Path $repositoryRoot "control-plane"
$certificateTool = Join-Path $controlPlaneRoot "tools\generate_dev_certificate.py"
$temporaryRoot = Join-Path $env:TEMP "squash-rmm-local-demo"
$certificateDirectory = Join-Path $temporaryRoot "tls"
$bootstrapVenv = Join-Path $temporaryRoot "certificate-venv"

New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $bootstrapVenv "Scripts\python.exe"))) {
    & python.exe -m venv $bootstrapVenv
    & (Join-Path $bootstrapVenv "Scripts\python.exe") -m pip install --disable-pip-version-check cryptography==46.0.7
}
& (Join-Path $bootstrapVenv "Scripts\python.exe") $certificateTool $Hostname $certificateDirectory

$certificatePath = Join-Path $certificateDirectory "server-cert.pem"
$keyPath = Join-Path $certificateDirectory "server-key.pem"
$hostsPath = "$env:WINDIR\System32\drivers\etc\hosts"
$escapedHostname = [Regex]::Escape($Hostname)
if (-not (Select-String -LiteralPath $hostsPath -Pattern "^\s*127\.0\.0\.1\s+$escapedHostname(?:\s|$)" -Quiet)) {
    Add-Content -LiteralPath $hostsPath -Value "`r`n127.0.0.1 $Hostname" -Encoding Ascii
}
Import-Certificate -FilePath $certificatePath -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
Import-Certificate -FilePath $certificatePath -CertStoreLocation "Cert:\CurrentUser\Root" | Out-Null

$randomBytes = New-Object byte[] 48
$random = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $random.GetBytes($randomBytes) }
finally { $random.Dispose() }
$apiKeyPlain = [Convert]::ToBase64String($randomBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
$apiKeySecure = ConvertTo-SecureString $apiKeyPlain -AsPlainText -Force
$publicBaseUrl = "https://${Hostname}:$Port"

& (Join-Path $PSScriptRoot "install-control-plane.ps1") `
    -SourceRoot $controlPlaneRoot `
    -ApiKey $apiKeySecure `
    -PublicBaseUrl $publicBaseUrl `
    -TlsCertificateFile $certificatePath `
    -TlsPrivateKeyFile $keyPath `
    -Port $Port

Set-Clipboard -Value $apiKeyPlain
$apiKeyPlain = $null
$apiKeySecure = $null

$ready = $false
for ($attempt = 1; $attempt -le 20; $attempt++) {
    try {
        $response = Invoke-RestMethod -Uri "$publicBaseUrl/ready" -TimeoutSec 3
        if ($response.status -eq "ok") { $ready = $true; break }
    } catch {
        Start-Sleep -Seconds 1
    }
}
if (-not $ready) {
    throw "The service was installed but its HTTPS readiness endpoint did not become available."
}

if (Test-Path -LiteralPath $temporaryRoot) {
    $resolvedTemporaryRoot = (Resolve-Path -LiteralPath $temporaryRoot).Path
    $expectedPrefix = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if (-not $resolvedTemporaryRoot.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove unexpected temporary path: $resolvedTemporaryRoot"
    }
    Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force
}

Write-Host "Control plane ready: $publicBaseUrl/"
Write-Host "Swagger: $publicBaseUrl/docs"
Write-Host "Execution console: $publicBaseUrl/console"
Write-Host "The generated API key is on your clipboard."
} catch {
    Write-Error $_
    throw
} finally {
    Stop-Transcript | Out-Null
}
