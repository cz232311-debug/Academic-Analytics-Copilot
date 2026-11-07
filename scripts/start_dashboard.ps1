param(
    [string]$ApiBaseUrl = 'http://127.0.0.1:8000',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8501
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Python environment is missing. Follow START_HERE.zh-CN.md first-time setup.'
}
$env:COPILOT_API_BASE_URL = $ApiBaseUrl
Write-Host "Dashboard: http://127.0.0.1:$Port"
Write-Host "FastAPI: $ApiBaseUrl. Keep both service windows open."
& $projectPython -m streamlit run streamlit_app.py --server.address=127.0.0.1 --server.port=$Port --server.headless=true --browser.gatherUsageStats=false
