param(
    [ValidateSet('mock', 'deepseek')]
    [string]$Provider = 'deepseek',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [string]$Database = 'data/workbench-demo.sqlite3',
    [switch]$ForcePrompt
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Python environment is missing. Follow START_HERE.zh-CN.md first-time setup.'
}
# A second backend on the same port silently fails to bind, leaving the page
# talking to the old process with its old key. Fail loudly instead.
if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
        throw "Port $Port is already in use by PID $($listener.OwningProcess). Stop that backend window with Ctrl+C first; otherwise the page keeps using the old process and any new key is ignored."
    }
}
$env:ACADEMIC_DB_PATH = $Database
$env:COPILOT_LLM_PROVIDER = $Provider
$keySource = 'not required (Mock)'
if ($Provider -eq 'deepseek') {
    $env:COPILOT_LLM_BASE_URL = 'https://api.deepseek.com'
    $env:COPILOT_LLM_MODEL = 'deepseek-flash'
    if ($ForcePrompt -and -not [string]::IsNullOrWhiteSpace($env:COPILOT_LLM_API_KEY)) {
        Write-Host '-ForcePrompt: ignoring the existing COPILOT_LLM_API_KEY in this PowerShell session.'
        $env:COPILOT_LLM_API_KEY = $null
    }
    if ([string]::IsNullOrWhiteSpace($env:COPILOT_LLM_API_KEY)) {
        $copilotSecret = Read-Host 'Paste the DeepSeek API key (hidden)' -AsSecureString
        try {
            $env:COPILOT_LLM_API_KEY = [System.Net.NetworkCredential]::new('', $copilotSecret).Password
        }
        finally {
            Remove-Variable copilotSecret
        }
        $keySource = 'interactive prompt (this window)'
    }
    else {
        $keySource = 'existing COPILOT_LLM_API_KEY in this PowerShell session'
    }
    if ([string]::IsNullOrWhiteSpace($env:COPILOT_LLM_API_KEY)) {
        throw 'The API key was empty. Nothing was started.'
    }
    Write-Host "DeepSeek key source: $keySource (value hidden)."
    Write-Host 'If the page later reports an authentication failure, restart with -ForcePrompt to re-enter the key.'
}
Write-Host "FastAPI provider=$Provider database=$Database port=$Port key=$keySource"
Write-Host "Keep this window open. Health: http://127.0.0.1:$Port/health"
& $projectPython -m uvicorn copilot.api:app --host 127.0.0.1 --port $Port
