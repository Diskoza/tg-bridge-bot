$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
if (-not (Test-Path $Python)) { throw "Сначала запустите setup_windows.cmd" }
$existing = Get-BotProcess
if ($null -ne $existing) {
    Write-Host "Бот уже работает, PID=$($existing.Id)"
    exit 0
}
$stamp = Get-Date -Format "yyyy-MM-dd"
$outLog = Join-Path $LogDir "bot-$stamp.log"
$errLog = Join-Path $LogDir "bot-$stamp-error.log"
$proc = Start-Process -FilePath $Python -ArgumentList @("-m", "app.main") -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
Set-Content -Path $PidFile -Value $proc.Id -Encoding ASCII
Start-Sleep -Seconds 3
if ($proc.HasExited) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Бот завершился с ошибкой. Смотрите $errLog" -ForegroundColor Red
    exit 1
}
Write-Host "Бот запущен скрыто, PID=$($proc.Id)" -ForegroundColor Green
