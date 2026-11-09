param(
    [ValidateSet("stdout","err")] $which = "stdout",
    [switch] $grep
)
$ErrorActionPreference = "Continue"

$logDir = Join-Path (Split-Path -Parent $PSScriptRoot) "foxclaw_data"
$log    = if ($which -eq "err") { "$logDir\uvicorn.err.log" } else { "$logDir\uvicorn.log" }

if (-not (Test-Path $log)) {
    Write-Host "[NOT FOUND] $log"
    exit 1
}

$fs = [System.IO.File]::Open($log, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
$reader = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
$content = $reader.ReadToEnd()
$reader.Close()
$fs.Close()

if ($grep) {
    foreach ($line in ($content -split "`n")) {
        if ($line -match "VectorIndex 升级|VectorIndex 升级失败") {
            Write-Host $line
        }
    }
    return
}

Write-Host "log file: $log (size=$($content.Length))"
Write-Host ("has_VectorIndex="     + $content.Contains("VectorIndex"))
Write-Host ("has_IVF="             + $content.Contains("IVF"))
Write-Host ("has_upgrade_zh="      + $content.Contains("升级"))
Write-Host ("has_IndexFlat="       + $content.Contains("IndexFlat"))
Write-Host ("has_IndexIVF="        + $content.Contains("IndexIVF"))
Write-Host "--- last 1500 chars ---"
$start = [Math]::Max(0, $content.Length - 1500)
Write-Host $content.Substring($start)
