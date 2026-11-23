$Script:Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Script:Python = Join-Path $Script:Root ".venv\Scripts\python.exe"
$Script:RunDir = Join-Path $Script:Root "run"
$Script:LogDir = Join-Path $Script:Root "logs"
$Script:PidFile = Join-Path $Script:RunDir "bot.pid"
New-Item -ItemType Directory -Force -Path $Script:RunDir, $Script:LogDir | Out-Null

function Get-BotProcess {
    if (-not (Test-Path $Script:PidFile)) { return $null }
    $raw = (Get-Content $Script:PidFile -Raw -ErrorAction SilentlyContinue).Trim()
    $pidValue = 0
    if (-not [int]::TryParse($raw, [ref]$pidValue)) {
        Remove-Item $Script:PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    $proc = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($null -eq $proc) {
        Remove-Item $Script:PidFile -Force -ErrorAction SilentlyContinue
        return $null
    }
    return $proc
}

function Set-EnvValue([string]$Key, [string]$Value) {
    $envPath = Join-Path $Script:Root ".env"
    $lines = if (Test-Path $envPath) { Get-Content $envPath -Encoding UTF8 } else { @() }
    $found = $false
    $result = foreach ($line in $lines) {
        if ($line -match "^$([regex]::Escape($Key))=") {
            "$Key=$Value"
            $found = $true
        } else { $line }
    }
    if (-not $found) { $result += "$Key=$Value" }
    [System.IO.File]::WriteAllLines($envPath, [string[]]$result, (New-Object System.Text.UTF8Encoding($false)))
}
