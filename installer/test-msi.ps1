[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $MsiPath
)

$ErrorActionPreference = "Stop"
$resolvedMsi = (Resolve-Path -LiteralPath $MsiPath).Path
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.GetType().InvokeMember(
    "OpenDatabase",
    "InvokeMethod",
    $null,
    $installer,
    @($resolvedMsi, 0)
)

function Read-MsiRows([string] $Sql, [int] $ColumnCount) {
    try {
        $view = $database.GetType().InvokeMember(
            "OpenView",
            "InvokeMethod",
            $null,
            $database,
            @($Sql)
        )
    } catch {
        throw "MSI query failed: $Sql"
    }
    $view.GetType().InvokeMember("Execute", "InvokeMethod", $null, $view, $null) | Out-Null
    while ($record = $view.GetType().InvokeMember("Fetch", "InvokeMethod", $null, $view, $null)) {
        $values = @()
        for ($column = 1; $column -le $ColumnCount; $column++) {
            $values += $record.GetType().InvokeMember(
                "StringData",
                "GetProperty",
                $null,
                $record,
                $column
            )
        }
        ,$values
    }
}

$properties = @{}
foreach ($row in (Read-MsiRows "SELECT ``Property``,``Value`` FROM ``Property``" 2)) {
    $properties[$row[0]] = $row[1]
}
if ($properties["ProductName"] -ne "Squash RMM Agent") {
    throw "Unexpected MSI product name."
}
if ($properties["SecureCustomProperties"] -notmatch "ENROLLMENT_TOKEN_FILE") {
    throw "Enrollment token file property is not marked secure."
}
if ($properties["MsiHiddenProperties"] -notmatch "ENROLLMENT_TOKEN_FILE") {
    throw "Enrollment token file property is not hidden from MSI logs."
}

$customAction = @(Read-MsiRows "SELECT ``Action``,``Type``,``Source``,``Target`` FROM ``CustomAction`` WHERE ``Action``='EnrollAgent'" 4)
if ($customAction.Count -ne 1) { throw "EnrollAgent custom action is missing." }
$customActionType = [int]$customAction[0][1]
foreach ($requiredFlag in @(1024, 2048, 8192)) {
    if (($customActionType -band $requiredFlag) -ne $requiredFlag) {
        throw "EnrollAgent custom action is missing security flag $requiredFlag."
    }
}
if ($customAction[0][3] -match "(?i)token=[^\[]") {
    throw "A plaintext enrollment token appears in the custom-action target."
}

$sequence = @(Read-MsiRows "SELECT ``Condition``,``Sequence`` FROM ``InstallExecuteSequence`` WHERE ``Action``='EnrollAgent'" 2)
if ($sequence.Count -ne 1 -or [int]$sequence[0][1] -ne 4001) {
    throw "EnrollAgent is not scheduled immediately after InstallFiles."
}

$service = @(Read-MsiRows "SELECT ``Name``,``StartType``,``Component_`` FROM ``ServiceInstall`` WHERE ``Name``='SquashAgent'" 3)
if ($service.Count -ne 1 -or [int]$service[0][1] -ne 2) {
    throw "SquashAgent automatic service registration is missing."
}

$agentFile = @(Read-MsiRows "SELECT ``FileSize``,``Version`` FROM ``File`` WHERE ``File``='AgentExe'" 2)
if ($agentFile.Count -ne 1 -or [long]$agentFile[0][0] -lt 10MB) {
    throw "The self-contained agent executable is missing from the MSI."
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedMsi).Hash
Write-Host "MSI validation passed."
Write-Host "SHA-256: $hash"
