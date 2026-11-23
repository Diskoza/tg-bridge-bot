$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$taskName = "TG Bridge Bot Backup"
$script = Join-Path $PSScriptRoot "backup.py"
$command = "`"$Python`" `"$script`""
& schtasks.exe /Create /TN $taskName /SC DAILY /ST 03:30 /RL LIMITED /TR $command /F | Out-Host
Write-Host "Ежедневный backup зарегистрирован." -ForegroundColor Green
