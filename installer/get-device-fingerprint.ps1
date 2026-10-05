$ErrorActionPreference = "Stop"

$machineGuid = Get-ItemPropertyValue `
    -LiteralPath "HKLM:\SOFTWARE\Microsoft\Cryptography" `
    -Name "MachineGuid"
if ([string]::IsNullOrWhiteSpace($machineGuid)) {
    throw "Windows MachineGuid is unavailable."
}

$material = [Text.Encoding]::UTF8.GetBytes(
    "squash-rmm-device-v1`0$($machineGuid.Trim())"
)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $hash = $sha.ComputeHash($material)
    (-join ($hash | ForEach-Object { $_.ToString("x2") }))
} finally {
    $sha.Dispose()
}
