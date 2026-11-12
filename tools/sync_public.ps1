param(
    [switch]$Apply,
    [string]$Message = "",
    [string]$Remote = "origin",
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

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
    "tools/sync_public.ps1",
    "tools/sync_obsidian.ps1"
)

$RootMdAllow = @("README.md", "README_CN.md", "CHANGELOG.md", "CHANGELOG_CN.md")

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

function Read-TreeEntries {
    param([string]$Ref)
    $out = & git -C $src -c core.quotepath=false ls-tree -r $Ref
    if ($LASTEXITCODE -ne 0) { throw "ls-tree $Ref failed" }
    $list = @()
    foreach ($e in $out) {
        if (-not $e) { continue }
        $tab = $e.IndexOf("`t")
        if ($tab -lt 0) { continue }
        $meta = $e.Substring(0, $tab) -split ' '
        $list += [pscustomobject]@{ Mode = $meta[0]; Sha = $meta[2]; Path = $e.Substring($tab + 1) }
    }
    return $list
}

function Remove-IndexPaths {
    param([string[]]$Paths)
    if (-not $Paths -or $Paths.Count -eq 0) { return }
    for ($i = 0; $i -lt $Paths.Count; $i += 50) {
        $chunk = $Paths[$i..([Math]::Min($i + 49, $Paths.Count - 1))]
        & git -C $src update-index --force-remove -- @chunk
        if ($LASTEXITCODE -ne 0) { throw "update-index --force-remove failed" }
    }
}

& git -C $src fetch --quiet $Remote $Branch
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] fetch $Remote/$Branch failed, using last known remote ref" -ForegroundColor Yellow
}
$base = (& git -C $src rev-parse "$Remote/$Branch").Trim()
if (-not $base) { Write-Host "[ABORT] cannot resolve $Remote/$Branch" -ForegroundColor Red; exit 1 }
$baseTree = (& git -C $src rev-parse "$base^{tree}").Trim()

$head = Read-TreeEntries "HEAD"
$prev = Read-TreeEntries $baseTree
$prevMap = @{}
foreach ($p in $prev) { $prevMap[$p.Path] = $p }
$headSet = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($h in $head) { $headSet.Add($h.Path) | Out-Null }

$fresh = @($head | Where-Object { -not (Test-Excluded $_.Path) })
$headExcluded = @($head | Where-Object { Test-Excluded $_.Path } | ForEach-Object { $_.Path })
$keepOld = @($prev | Where-Object { (Test-Excluded $_.Path) -and $headSet.Contains($_.Path) })
$stale = @($prev | Where-Object { -not $headSet.Contains($_.Path) } | ForEach-Object { $_.Path })

$idx = Join-Path ([IO.Path]::GetTempPath()) ("foxclaw-pub-" + [guid]::NewGuid().ToString("N") + ".idx")
$env:GIT_INDEX_FILE = $idx
try {
    & git -C $src read-tree HEAD
    if ($LASTEXITCODE -ne 0) { throw "read-tree HEAD failed" }

    Remove-IndexPaths $headExcluded

    foreach ($k in $keepOld) {
        & git -C $src update-index --add --cacheinfo "$($k.Mode),$($k.Sha),$($k.Path)"
        if ($LASTEXITCODE -ne 0) { throw "restore failed: $($k.Path)" }
    }

    Remove-IndexPaths $stale

    $tree = (& git -C $src write-tree).Trim()
    if (-not $tree) { throw "write-tree returned nothing" }
}
finally {
    Remove-Item Env:\GIT_INDEX_FILE -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $idx) { Remove-Item -LiteralPath $idx -Force -ErrorAction SilentlyContinue }
}

$treeFiles = @(& git -C $src -c core.quotepath=false ls-tree -r --name-only $tree)
$prevExcluded = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($p in $prev) { if (Test-Excluded $p.Path) { $prevExcluded.Add($p.Path) | Out-Null } }
$leak = @($treeFiles | Where-Object { (Test-Excluded $_) -and -not $prevExcluded.Contains($_) })
if ($leak.Count -gt 0) {
    Write-Host "[ABORT] new internal paths inside candidate tree:" -ForegroundColor Red
    $leak | Select-Object -First 20 | ForEach-Object { Write-Host "  ! $_" -ForegroundColor Red }
    exit 1
}

Write-Host ("public tree: {0} files | from HEAD: {1} | kept as-is: {2} | removed: {3}" -f $treeFiles.Count, $fresh.Count, $keepOld.Count, $stale.Count)

if ($tree -eq $baseTree) {
    Write-Host "no changes."
    return
}

Write-Host ("`nchanges vs {0}/{1} ({2}):" -f $Remote, $Branch, $base.Substring(0, 7))
& git -C $src --no-pager diff --stat $baseTree $tree | Select-Object -First 80

$msg = if ($Message) { $Message } else { "sync: update from development repo" }
$commit = (& git -C $src commit-tree $tree -p $base -m $msg).Trim()
if (-not $commit) { Write-Host "[ABORT] commit-tree failed" -ForegroundColor Red; exit 1 }
Write-Host "`ncandidate commit: $commit (parent $($base.Substring(0,7)))"

if (-not $Apply) {
    Write-Host "`n(dry-run) re-run with -Apply to push."
    return
}

& git -C $src push $Remote "${commit}:refs/heads/$Branch"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ABORT] push failed; nothing changed on $Remote." -ForegroundColor Red
    exit 1
}
Write-Host "`npushed $($commit.Substring(0,7)) to $Remote/$Branch." -ForegroundColor Green
