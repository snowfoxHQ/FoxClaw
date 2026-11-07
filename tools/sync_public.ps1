# tools/sync_public.ps1
# 把私有开发库导出到公开仓库。默认只打印计划并做越界检查,-Apply 才真正复制、提交、推送。
#
# 为什么需要它:此前同步靠临时脚本整树复制,既没有持久化的排除清单,又会把
# .gitignore 一起覆盖掉(公开仓库的忽略规则因此退回成私有库的规则),内部文档
# 就这么进过一次公开历史。这里把清单固化在脚本里,并在提交前做一次断言。
#
# 注意:本文件必须保持以下约束,否则 Windows PowerShell 5.1 会解析失败 ——
# 无 BOM 的 .ps1 按 ANSI 读取,所以字符串字面量一律用 ASCII,中文只出现在注释里。
#
# 用法:
#   powershell -NoProfile -File tools\sync_public.ps1              # 只看计划
#   powershell -NoProfile -File tools\sync_public.ps1 -Apply       # 真正同步
param(
    [switch]$Apply,
    [string]$PublicRepo = "C:\Users\snow\projects\FoxClaw-public",
    [string]$Message = ""
)

$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot

# ── 绝不外发的目录(相对仓库根,前缀匹配) ─────────────────────────
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

# ── 绝不外发的单文件(精确匹配) ──────────────────────────────────
# .gitignore 必须排除:公开仓库的忽略规则是独立维护的,不能被私有库覆盖。
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

# 根目录散落的笔记(临时想法、方案草稿)属于个人工作区,一律不外发;
# 根目录 .md 只放行下面这两个。
$RootMdAllow = @("README.md", "README_CN.md")

# ── 绝不外发的模式(通配) ────────────────────────────────────────
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
    # .env 的衍生备份一律不发,模板放行
    if ($r -match '(^|/)\.env' -and $r -notmatch '\.env\.example$') { return $true }
    # 根目录 .md 只放行白名单
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

Write-Host ("files to sync: {0}  (target: {1})" -f $plan.Count, $PublicRepo)
if ($plan.Count -eq 0) { Write-Host "no changes."; return }
$plan | Select-Object -First 40 | ForEach-Object { Write-Host "  + $_" }
if ($plan.Count -gt 40) { Write-Host ("  ... and {0} more" -f ($plan.Count - 40)) }

if (-not $Apply) {
    Write-Host "`n(dry-run) re-run with -Apply to sync."
    return
}

# ── 复制(只增改,不删除目标侧文件) ──────────────────────────────
foreach ($rel in $plan) {
    $dest = Join-Path $PublicRepo $rel
    $dir = Split-Path -Parent $dest
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item (Join-Path $src $rel) $dest -Force
}

# ── 提交前断言:暂存区里不允许出现任何内部路径 ─────────────────────
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

# 推送失败必须显式报错:此前漏检退出码,网络被重置时脚本照样打印
# "synced and pushed",而远端其实没收到任何东西。
& git -C $PublicRepo push origin main
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ABORT] push failed; commit is local only. re-run -Apply to retry." -ForegroundColor Red
    exit 1
}
Write-Host "`nsynced and pushed." -ForegroundColor Green
