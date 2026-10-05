[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $DestinationDirectory,
    [string] $DataRoot = "C:\ProgramData\SquashRMM\ControlPlane"
)

$ErrorActionPreference = "Stop"
$expectedDataRoot = "C:\ProgramData\SquashRMM\ControlPlane"
if ([IO.Path]::GetFullPath($DataRoot).TrimEnd('\') -ne $expectedDataRoot) {
    throw "Refusing backup because DataRoot differs from the known Squash RMM location."
}

$destinationRoot = [IO.Path]::GetFullPath($DestinationDirectory)
New-Item -ItemType Directory -Force -Path $destinationRoot | Out-Null
$backupDirectory = Join-Path $destinationRoot (Get-Date -Format "yyyyMMdd-HHmmss")
New-Item -ItemType Directory -Force -Path $backupDirectory | Out-Null
& icacls.exe $backupDirectory /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null

$service = Get-Service -Name "SquashRMMControlPlane" -ErrorAction Stop
$wasRunning = $service.Status -eq "Running"
try {
    if ($wasRunning) { Stop-Service -Name "SquashRMMControlPlane" -Force }
    foreach ($name in @("squash-rmm.db", "squash-rmm.db-wal", "squash-rmm.db-shm", "control-plane.env")) {
        $source = Join-Path $DataRoot $name
        if (Test-Path -LiteralPath $source) {
            Copy-Item -LiteralPath $source -Destination $backupDirectory -Force
        }
    }
} finally {
    if ($wasRunning) { Start-Service -Name "SquashRMMControlPlane" }
}

Write-Host "Backup created at $backupDirectory"
