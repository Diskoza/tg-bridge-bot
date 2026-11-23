$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$proc = Get-BotProcess
if ($null -eq $proc) { Write-Host "Бот не запущен."; exit 0 }
Stop-Process -Id $proc.Id -Force
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "Бот остановлен." -ForegroundColor Green
