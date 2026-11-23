. (Join-Path $PSScriptRoot "common.ps1")
$proc = Get-BotProcess
if ($null -eq $proc) { Write-Host "Бот не запущен." -ForegroundColor Yellow }
else {
    Write-Host "Бот работает. PID=$($proc.Id), CPU=$([Math]::Round($proc.CPU,2)) сек, RAM=$([Math]::Round($proc.WorkingSet64/1MB,1)) МБ" -ForegroundColor Green
}
Write-Host "Последние логи:"
Get-ChildItem $LogDir -Filter "bot-*.log" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 2 | ForEach-Object { Write-Host $_.FullName }
