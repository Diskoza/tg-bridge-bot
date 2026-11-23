$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
Set-Location $Root

function Resolve-PythonCommand {
    $candidates = @(
        @{Exe="py"; Args=@("-3.13")},
        @{Exe="py"; Args=@("-3.12")},
        @{Exe="py"; Args=@("-3.11")},
        @{Exe="python"; Args=@()}
    )
    foreach ($candidate in $candidates) {
        try {
            $version = & $candidate.Exe @($candidate.Args) -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $parts = $version.Trim().Split('.')
                if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { return $candidate }
            }
        } catch {}
    }
    return $null
}

$pythonCmd = Resolve-PythonCommand
if ($null -eq $pythonCmd) {
    Write-Host "Python 3.11+ не найден. Пытаюсь установить Python 3.12 через winget..." -ForegroundColor Yellow
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
        $pythonCmd = Resolve-PythonCommand
    }
}
if ($null -eq $pythonCmd) {
    throw "Установите Python 3.11-3.13 x64, отметьте Add Python to PATH и повторите setup_windows.cmd."
}

if (-not (Test-Path $Python)) {
    Write-Host "Создание виртуального окружения..."
    & $pythonCmd.Exe @($pythonCmd.Args) -m venv (Join-Path $Root ".venv")
}

& $Python -m pip install --upgrade pip wheel
& $Python -m pip install -r (Join-Path $Root "requirements.txt")

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
    Write-Host "Создан файл .env. Сейчас укажем токен." -ForegroundColor Cyan
}

& (Join-Path $PSScriptRoot "configure.ps1")

Write-Host "Проверка синтаксиса..."
& $Python -m compileall -q (Join-Path $Root "app")

$answer = Read-Host "Добавить автоматический запуск при входе в Windows? (Y/n)"
if ($answer -notmatch '^(n|no|нет)$') {
    & (Join-Path $PSScriptRoot "register_autostart.ps1")
}

$backupAnswer = Read-Host "Добавить ежедневную резервную копию SQLite? (Y/n)"
if ($backupAnswer -notmatch '^(n|no|нет)$') {
    & (Join-Path $PSScriptRoot "register_backup.ps1")
}

Write-Host "Установка завершена." -ForegroundColor Green
Write-Host "Запуск в окне: run_bot.cmd"
Write-Host "Скрытый запуск: start_hidden.cmd"
Write-Host "Для качественного сокращения запустите install_ollama_qwen.cmd"
