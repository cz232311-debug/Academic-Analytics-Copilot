<#!
Zero-token DeepSeek credential check.

Calls only the official GET /user/balance endpoint, which DeepSeek documents as
free and does not consume tokens. It never prints the key, its length or the
balance amount, and never writes anything to disk. Safe to run while the
backend is still listening: it does not start, stop or reconfigure a service.
!#>
param(
    [switch]$Prompt
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 can otherwise negotiate an older TLS version and make
# a perfectly good key look like a network failure.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$key = $env:COPILOT_LLM_API_KEY
if ($Prompt -or [string]::IsNullOrWhiteSpace($key)) {
    $secret = Read-Host 'Paste the DeepSeek API key to check (hidden)' -AsSecureString
    try {
        $key = [System.Net.NetworkCredential]::new('', $secret).Password.Trim()
    }
    finally {
        Remove-Variable secret -ErrorAction SilentlyContinue
    }
}
else {
    $key = $key.Trim()
}

if ([string]::IsNullOrWhiteSpace($key)) {
    Write-Host 'RESULT: no key was provided; nothing was sent.'
    exit 1
}
if ($key -match '\s') {
    Write-Warning 'The key still contains whitespace inside it (likely a partial or doubled paste). The value is hidden.'
}

Write-Host 'Checking the credential against GET /user/balance (no tokens are consumed)...'
try {
    $result = Invoke-RestMethod -Uri 'https://api.deepseek.com/user/balance' `
        -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 20
    if ($result.is_available) {
        Write-Host 'RESULT: credential accepted; the account can be billed for API calls.'
        Write-Host 'If queries still fail, restart the backend with -ForcePrompt so this exact key is the one in use.'
        exit 0
    }
    Write-Host 'RESULT: credential accepted, but the account has no usable balance.'
    Write-Host 'Top up at platform.deepseek.com. Chat queries then return HTTP 402 (mapped to 503 here), not 502.'
    exit 2
}
catch {
    $status = $null
    if ($_.Exception.Response) {
        try { $status = [int]$_.Exception.Response.StatusCode } catch { }
    }
    if ($status -eq 401) {
        Write-Host 'RESULT: DeepSeek rejected this credential (HTTP 401).'
        Write-Host 'The key is invalid, deleted or rotated, copied incompletely, or belongs to another platform.'
        Write-Host 'Create a new key at platform.deepseek.com (it is shown only once), then restart the backend with -ForcePrompt.'
        exit 3
    }
    if ($status) {
        Write-Host "RESULT: DeepSeek returned HTTP $status. Check platform status or retry later."
        exit 4
    }
    Write-Host 'RESULT: the request did not reach DeepSeek (network, DNS or proxy).'
    Write-Host 'The backend uses trust_env=False and does not read system proxy settings, so a proxy-only network fails with llm_connection_error.'
    exit 5
}
