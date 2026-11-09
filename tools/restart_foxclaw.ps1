$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $PSScriptRoot

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[1] WARN: .venv missing, fallback to PATH python (see README quick start)"
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $py) { $py = "python" }
Write-Host "[1] python = $py"

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

$logDir = Join-Path $root "foxclaw_data"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$outLog = Join-Path $logDir "uvicorn.log"
$errLog = Join-Path $logDir "uvicorn.err.log"
"" | Out-File -FilePath $outLog -Encoding utf8
"" | Out-File -FilePath $errLog -Encoding utf8
Write-Host "[3] Start service (hidden window, --reload)"
Write-Host "    stdout -> $outLog"
Write-Host "    stderr -> $errLog"
$uviArgs = @(
    "-m", "uvicorn", "foxclaw.api:app",
    "--port", "8000",
    "--reload",
    "--reload-dir", (Join-Path $root "foxclaw")
)
Start-Process $py -ArgumentList $uviArgs `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError  $errLog | Out-Null
Write-Host "  -> Started, polling /stats up to 30s"

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
