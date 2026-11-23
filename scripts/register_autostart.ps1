$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$taskName = "TG Bridge Bot Windows"
$script = Join-Path $PSScriptRoot "start_bot.ps1"
$command = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
& schtasks.exe /Create /TN $taskName /SC ONLOGON /RL LIMITED /TR $command /F | Out-Host
Write-Host "Автозапуск зарегистрирован: $taskName" -ForegroundColor Green
