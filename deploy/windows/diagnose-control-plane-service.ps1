$ErrorActionPreference = "Stop"
$pythonCommand = "C:\Program Files\SquashRMM\ControlPlane\.venv\Scripts\python.exe"
$outputPath = Join-Path $env:TEMP "squash-rmm-service-debug.log"
$registryPath = "HKLM:\SYSTEM\CurrentControlSet\Services\SquashRMMControlPlane"

Get-ItemProperty -LiteralPath $registryPath |
    Select-Object ImagePath, ObjectName |
    Format-List | Out-File -LiteralPath $outputPath -Encoding UTF8
foreach ($subkey in @("PythonClass", "PythonPath")) {
    $path = Join-Path $registryPath $subkey
    if (Test-Path -LiteralPath $path) {
        $value = (Get-Item -LiteralPath $path).GetValue("")
        "$subkey=$value" | Add-Content -LiteralPath $outputPath -Encoding UTF8
    }
}

& $pythonCommand -c "from squash_control_plane.config import get_settings; s=get_settings(); print(f'environment={s.environment} database={s.database_url} public={s.public_base_url}'); from squash_control_plane.main import app; print('application_import=ok')" 2>&1 |
    Out-File -LiteralPath $outputPath -Encoding UTF8 -Append
exit $LASTEXITCODE
