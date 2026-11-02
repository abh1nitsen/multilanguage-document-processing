param(
    [string]$SecretsDir = "",
    [string]$Database = "data/notices.sqlite",
    [int]$Port = 8501
)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
try {
    if ($SecretsDir) {
        $keyPath = Join-Path $SecretsDir "groq_key.txt"
        $env:GROQ_API_KEY = (Get-Content -Raw -LiteralPath $keyPath).Trim()
    }
    $env:CIRCULAR_DB = $Database
    & .\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port $Port --server.headless true --browser.gatherUsageStats false --server.maxUploadSize 15
} finally {
    Pop-Location
}
