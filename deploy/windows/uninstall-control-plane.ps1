[CmdletBinding(SupportsShouldProcess)]
param(
    [string] $InstallRoot = "C:\Program Files\SquashRMM\ControlPlane",
    [string] $DataRoot = "C:\ProgramData\SquashRMM\ControlPlane",
    [switch] $RemoveData
)

$ErrorActionPreference = "Stop"
$expectedInstallRoot = "C:\Program Files\SquashRMM\ControlPlane"
$expectedDataRoot = "C:\ProgramData\SquashRMM\ControlPlane"
if ($InstallRoot -ne $expectedInstallRoot -or $DataRoot -ne $expectedDataRoot) {
    throw "Refusing removal because the paths differ from the known Squash RMM locations."
}

$service = Get-Service -Name "SquashRMMControlPlane" -ErrorAction SilentlyContinue
if ($service -and $PSCmdlet.ShouldProcess("SquashRMMControlPlane", "remove Windows service")) {
    Stop-Service -Name "SquashRMMControlPlane" -Force -ErrorAction SilentlyContinue
    $serviceCommand = Join-Path $InstallRoot ".venv\Scripts\squash-control-plane-service.exe"
    if (Test-Path -LiteralPath $serviceCommand) {
        & $serviceCommand remove
    }
}

$taskName = "SquashRMMControlPlaneStartup"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task -and $PSCmdlet.ShouldProcess($taskName, "remove startup task")) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Get-NetFirewallRule -DisplayName "Squash RMM Control Plane HTTPS" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule

if ((Test-Path -LiteralPath $InstallRoot) -and $PSCmdlet.ShouldProcess($InstallRoot, "remove application files")) {
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}
if ($RemoveData -and (Test-Path -LiteralPath $DataRoot) -and $PSCmdlet.ShouldProcess($DataRoot, "remove configuration and TLS keys")) {
    Remove-Item -LiteralPath $DataRoot -Recurse -Force
}
