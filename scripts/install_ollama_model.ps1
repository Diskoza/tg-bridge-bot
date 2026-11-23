$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Ollama не найден. Запускаю официальный установщик..." -ForegroundColor Yellow
    irm https://ollama.com/install.ps1 | iex
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
}
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Перезапустите PowerShell после установки Ollama и снова запустите этот файл."
}
Write-Host "Загрузка qwen3:8b..." -ForegroundColor Cyan
ollama pull qwen3:8b
Set-EnvValue "SUMMARY_BACKEND" "local_llm"
Set-EnvValue "SUMMARY_API_URL" "http://127.0.0.1:11434/v1/chat/completions"
Set-EnvValue "SUMMARY_MODEL" "qwen3:8b"
Write-Host "Ollama и модель готовы. Перезапустите бота." -ForegroundColor Green
