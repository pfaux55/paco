param()

$ErrorActionPreference = "Stop"
$ollamaUrl = "http://127.0.0.1:11434/api/tags"

try {
    Invoke-RestMethod -Uri $ollamaUrl -Method Get -TimeoutSec 3 | Out-Null
    exit 0
} catch {
}

$ollamaCommand = Get-Command ollama.exe -ErrorAction SilentlyContinue
if (-not $ollamaCommand) {
    Write-Error "Ollama was not found on PATH. Install Ollama for Windows or add it to PATH."
    exit 1
}

$ollamaDir = Split-Path -Parent $ollamaCommand.Source
$ollamaApp = Join-Path $ollamaDir "ollama app.exe"

if (Test-Path $ollamaApp) {
    Start-Process -FilePath $ollamaApp | Out-Null
} else {
    Start-Process -FilePath $ollamaCommand.Source -ArgumentList "serve" -WindowStyle Hidden | Out-Null
}

Start-Sleep -Seconds 4

try {
    Invoke-RestMethod -Uri $ollamaUrl -Method Get -TimeoutSec 3 | Out-Null
} catch {
    Write-Error "Ollama did not respond on http://127.0.0.1:11434 after startup."
    exit 1
}
