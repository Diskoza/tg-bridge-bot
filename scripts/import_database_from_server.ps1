$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$server = Read-Host "IP сервера"
if (-not $server) { throw "Не указан IP сервера." }
$user = Read-Host "SSH пользователь [host]"
if (-not $user) { $user = "host" }
& (Join-Path $PSScriptRoot "stop_bot.ps1")
$dataDir = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
$target = Join-Path $dataDir "bridge.sqlite3"
if (Test-Path $target) {
    $backup = Join-Path $dataDir ("bridge_before_import_{0}.sqlite3" -f (Get-Date -Format "yyyyMMdd_HHmmss"))
    Copy-Item $target $backup
    Write-Host "Локальная база сохранена: $backup"
}
& scp.exe "${user}@${server}:/opt/tg-bridge/data/bridge.sqlite3" $target
if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать базу с сервера." }
Write-Host "База импортирована. Не запускайте одновременно серверную и Windows-версию с одним токеном." -ForegroundColor Green
