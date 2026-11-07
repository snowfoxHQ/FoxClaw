# tools/restart_foxclaw.ps1
# 杀旧 uvicorn → 后台启新(带 --reload 自动加载 vendor 修改) → 等 /stats 可达
# 用法: powershell -NoProfile -File tools\restart_foxclaw.ps1
$ErrorActionPreference = "Continue"

# 1. python 路径
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = "python" }
Write-Host "[1] python = $py"

# 2. 找 LISTENING on :8000
Write-Host "[2] Find LISTENING on :8000"
$targetPid = $null
$netstatLines = netstat -ano | Select-String ":8000"
foreach ($line in $netstatLines) {
    $s = $line.ToString()
    if ($s -match 'LISTENING\s+(\d+)$') {
        $targetPid = $Matches[1]
        break
    }
}

if ($targetPid) {
    Write-Host "  -> Kill PID $targetPid"
    taskkill /F /PID $targetPid | Out-Null
    Start-Sleep -Seconds 2
} else {
    Write-Host "  -> No LISTEN on :8000 (may already be down)"
}

# 3. 启 uvicorn(后台,隐藏窗口,带 --reload + 日志 redirect)
$logDir = "C:\Users\snow\projects\Foxclaw\foxclaw_data"
$outLog = "$logDir\uvicorn.log"
$errLog = "$logDir\uvicorn.err.log"
"" | Out-File -FilePath $outLog -Encoding utf8
"" | Out-File -FilePath $errLog -Encoding utf8
Write-Host "[3] Start uvicorn (hidden window, --reload --reload-dir vendor)"
Write-Host "    stdout -> $outLog"
Write-Host "    stderr -> $errLog"
$uviArgs = @(
    "-m", "uvicorn", "foxclaw.api:app",
    "--port", "8000",
    "--reload",
    "--reload-dir", "C:\Users\snow\projects\Foxclaw\vendor"
)
Start-Process $py -ArgumentList $uviArgs `
    -WorkingDirectory "C:\Users\snow\projects\Foxclaw" `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError  $errLog | Out-Null
Write-Host "  -> Started, polling /stats up to 30s"

# 4. 轮询 /stats 等就绪
$ready = $false
for ($i=0; $i -lt 15; $i++) {
    try {
        $s = Invoke-RestMethod http://127.0.0.1:8000/stats -TimeoutSec 2 -UseBasicParsing
        if ($s) {
            $ready = $true
            Write-Host ("[4] OK: vector_size={0} bm25={1} state={2} old_pid={3}" -f `
                $s.vector_size, $s.bm25_size, $s.index_build.index_state, $targetPid)
            break
        }
    } catch {}
    Start-Sleep -Seconds 2
}

if (-not $ready) {
    Write-Host "[4] FAIL: service did not respond in 30s"
    exit 1
}
