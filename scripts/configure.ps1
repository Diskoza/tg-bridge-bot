$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$envPath = Join-Path $Root ".env"
if (-not (Test-Path $envPath)) { Copy-Item (Join-Path $Root ".env.example") $envPath }

$current = Get-Content $envPath -Encoding UTF8
$tokenLine = $current | Where-Object { $_ -match '^BOT_TOKEN=' } | Select-Object -First 1
$currentToken = if ($tokenLine) { $tokenLine.Substring(10) } else { "" }
if ($currentToken -match 'ВСТАВЬТЕ|^123456789:' -or -not $currentToken) {
    $token = Read-Host "Вставьте токен Telegram-бота от @BotFather"
    if ($token -and $token.Contains(':')) { Set-EnvValue "BOT_TOKEN" $token }
    else { throw "Корректный BOT_TOKEN не указан." }
} else {
    $token = Read-Host "Новый токен или Enter, чтобы оставить текущий"
    if ($token) {
        if (-not $token.Contains(':')) { throw "Токен выглядит неверно." }
        Set-EnvValue "BOT_TOKEN" $token
    }
}

$threads = [Math]::Max(2, [Math]::Min(8, [Environment]::ProcessorCount - 1))
Set-EnvValue "WHISPER_CPU_THREADS" "$threads"

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Set-EnvValue "SUMMARY_BACKEND" "local_llm"
    Set-EnvValue "SUMMARY_API_URL" "http://127.0.0.1:11434/v1/chat/completions"
    Set-EnvValue "SUMMARY_MODEL" "qwen3:8b"
} else {
    Set-EnvValue "SUMMARY_BACKEND" "extractive"
    Write-Host "Ollama пока не найден: включено быстрое безопасное сокращение без LLM." -ForegroundColor Yellow
}
Write-Host "Конфигурация сохранена: $envPath" -ForegroundColor Green
