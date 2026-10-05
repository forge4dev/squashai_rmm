[CmdletBinding()]
param(
    [string] $ApiKey = "development-only-key",
    [int] $Port = 8000
)

$bytes = [Text.Encoding]::UTF8.GetBytes($ApiKey)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $hashBytes = $sha.ComputeHash($bytes)
    $env:SQUASH_API_KEY_SHA256 = -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
}
finally { $sha.Dispose() }
$env:SQUASH_ENVIRONMENT = "development"
$env:SQUASH_DATABASE_URL = "sqlite+pysqlite:///./squash-dev.db"
$env:SQUASH_PUBLIC_BASE_URL = "http://127.0.0.1:$Port"

& .\.venv\Scripts\python.exe -m uvicorn squash_control_plane.main:app --host 127.0.0.1 --port $Port
