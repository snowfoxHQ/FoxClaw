# tools/check_log.ps1
# 检查 uvicorn 日志中是否有 IVF 升级关键字(行扫描 + FileShare.ReadWrite 兼容锁文件)。
# 用法:
#   powershell -NoProfile -File tools\check_log.ps1            # 查 stdout(uvicorn.log)
#   powershell -NoProfile -File tools\check_log.ps1 err        # 查 stderr(uvicorn.err.log)
#   powershell -NoProfile -File tools\check_log.ps1 err grep   # 仅 grep 'VectorIndex 升级' 行
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

# 用 [System.IO.File]::Open + FileShare.ReadWrite 兼容 python 文件正在写入的日志
# (PowerShell 5.1 Get-Content 在 UTF-8 + 文件被锁场景会返空,见 review [2])
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
