param(
    [switch]$Apply,
    [string]$PublicRepo = "C:\Users\snow\projects\FoxClaw-public",
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot

$ExcludeDirs = @(
    ".git/",
    "docs/internal/",
    "docs/superpowers/",
    "docs/reports/",
    ".claude/",
    ".workbuddy/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".venv/",
    "venv/",
    "build/",
    "dist/",
    "htmlcov/",
    "foxclaw_data/",
    "test_data/",
    "node_modules/",
    "npm/python/",
    "frontend_src/node_modules/",
    "frontend_src/.vite/"
)

$ExcludeFiles = @(
    ".gitignore",
    ".env",
    ".vscode/settings.json",
    ".tmp_srv.out",
    ".tmp_srv.err",
    "docs/FIX_EXECUTION_REPORT.md",
    "docs/REFACTOR_PLAN.md",
    "tools/sync_public.ps1"
)

$RootMdAllow = @("README.md", "README_CN.md")

$ExcludeGlobs = @(
    "docs/SYSTEM_REVIEW_*.md",
    "*.pyc",
    "*.pyo",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    "*.bak",
    "*.bak-*",
    "*.log",
    "*.err",
    "frontend_src/*.log",
    "frontend_src/*.err",
    "tests/_fail_*.txt",
    "*_test_data/*",
    "*.egg-info/*"
)

function Test-Excluded {
    param([string]$Rel)
    $r = $Rel.Replace('\', '/')
    foreach ($d in $ExcludeDirs) {
        if ($r -like "$d*" -or $r -like "*/$d*") { return $true }
    }
    foreach ($f in $ExcludeFiles) {
        if ($r -ieq $f) { return $true }
    }
    foreach ($g in $ExcludeGlobs) {
        if ($r -like $g) { return $true }
    }
    if ($r -match '(^|/)\.env' -and $r -notmatch '\.env\.example$') { return $true }
    if ($r -notmatch '/' -and $r -match '\.md$' -and ($RootMdAllow -notcontains $r)) { return $true }
    return $false
}

if (-not (Test-Path $PublicRepo)) { throw "public repo not found: $PublicRepo" }

$plan = @()
foreach ($f in Get-ChildItem -Path $src -Recurse -File -Force) {
    $rel = $f.FullName.Substring($src.Length + 1)
    if (Test-Excluded $rel) { continue }
    $dest = Join-Path $PublicRepo $rel
    if (-not (Test-Path $dest) -or (Get-FileHash $f.FullName -Algorithm MD5).Hash -ne (Get-FileHash $dest -Algorithm MD5).Hash) {
        $plan += $rel
    }
}

$srcSet = [System.Collections.Generic.HashSet[string]]::new()
foreach ($f in Get-ChildItem -Path $src -Recurse -File -Force) {
    $srcSet.Add($f.FullName.Substring($src.Length + 1).Replace('\', '/')) | Out-Null
}
$stale = @()
foreach ($rel in (& git -C $PublicRepo -c core.quotepath=false ls-files)) {
    if (-not $rel) { continue }
    $norm = $rel.Replace('\', '/')
    if ($srcSet.Contains($norm)) { continue }
    if (Test-Excluded $norm) { continue }
    $stale += $rel
}

Write-Host ("files to sync: {0}  (target: {1})" -f $plan.Count, $PublicRepo)
if ($plan.Count -eq 0 -and $stale.Count -eq 0) { Write-Host "no changes."; return }
$plan | Select-Object -First 40 | ForEach-Object { Write-Host "  + $_" }
if ($plan.Count -gt 40) { Write-Host ("  ... and {0} more" -f ($plan.Count - 40)) }
if ($stale.Count -gt 0) {
    Write-Host ("files to delete: {0}" -f $stale.Count)
    $stale | Select-Object -First 40 | ForEach-Object { Write-Host "  - $_" }
    if ($stale.Count -gt 40) { Write-Host ("  ... and {0} more" -f ($stale.Count - 40)) }
}

if (-not $Apply) {
    Write-Host "`n(dry-run) re-run with -Apply to sync."
    return
}

foreach ($rel in $plan) {
    $dest = Join-Path $PublicRepo $rel
    $dir = Split-Path -Parent $dest
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item (Join-Path $src $rel) $dest -Force
}

foreach ($rel in $stale) {
    & git -C $PublicRepo rm --quiet -- $rel | Out-Null
}

& git -C $PublicRepo add -A | Out-Null
$staged = & git -C $PublicRepo diff --cached --name-only
$bad = @($staged | Where-Object { Test-Excluded $_ })
if ($bad.Count -gt 0) {
    Write-Host "`n[ABORT] internal paths staged, index reset:" -ForegroundColor Red
    $bad | ForEach-Object { Write-Host "  ! $_" -ForegroundColor Red }
    & git -C $PublicRepo reset | Out-Null
    exit 1
}
if (-not $staged) { Write-Host "nothing staged."; return }

$msg = if ($Message) { $Message } else { "sync: update from development repo" }
& git -C $PublicRepo commit --quiet -m $msg
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ABORT] commit failed." -ForegroundColor Red
    exit 1
}

& git -C $PublicRepo push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ABORT] push failed; commit is local only. re-run -Apply to retry." -ForegroundColor Red
    exit 1
}
Write-Host "`nsynced and pushed." -ForegroundColor Green
