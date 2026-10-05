[CmdletBinding()]
param(
    [string] $DotNet = "dotnet.exe"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$project = Join-Path $PSScriptRoot "SquashAgent.Package\SquashAgent.Package.wixproj"

& $DotNet restore $project --configfile (Join-Path $repositoryRoot "NuGet.Config")
if ($LASTEXITCODE -ne 0) { throw "MSI dependency restore failed." }

& $DotNet build $project -c Release --no-restore
if ($LASTEXITCODE -ne 0) { throw "MSI build failed." }

$msi = Join-Path $repositoryRoot "artifacts\msi\SquashAgent.msi"
if (-not (Test-Path -LiteralPath $msi)) {
    throw "The expected MSI was not produced."
}

& (Join-Path $PSScriptRoot "test-msi.ps1") -MsiPath $msi
if ($LASTEXITCODE -ne 0) { throw "MSI table validation failed." }

Write-Host "MSI created: $msi"
