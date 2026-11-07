param(
    [ValidateSet('mock', 'deepseek')]
    [string]$Provider = 'mock',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [string]$Database = 'data/workbench-demo.sqlite3'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Python environment is missing. Follow START_HERE.zh-CN.md first-time setup.'
}
$env:ACADEMIC_DB_PATH = $Database
$env:COPILOT_LLM_PROVIDER = $Provider
if ($Provider -eq 'deepseek') {
    $env:COPILOT_LLM_BASE_URL = 'https://api.deepseek.com'
    $env:COPILOT_LLM_MODEL = 'deepseek-flash'
    if ([string]::IsNullOrWhiteSpace($env:COPILOT_LLM_API_KEY)) {
        $copilotSecret = Read-Host 'Paste the DeepSeek API key (hidden)' -AsSecureString
        try {
            $env:COPILOT_LLM_API_KEY = [System.Net.NetworkCredential]::new('', $copilotSecret).Password
        }
        finally {
            Remove-Variable copilotSecret
        }
    }
    if ([string]::IsNullOrWhiteSpace($env:COPILOT_LLM_API_KEY)) {
        throw 'The API key was empty. Nothing was started.'
    }
}
Write-Host "FastAPI provider=$Provider database=$Database"
Write-Host "Keep this window open. Health: http://127.0.0.1:$Port/health"
& $projectPython -m uvicorn copilot.api:app --host 127.0.0.1 --port $Port
