param(
    [switch]$Apply,
    [string]$VaultPath = "",
    [string]$FolderName = "FoxClaw"
)

$ErrorActionPreference = "Stop"
$src = Split-Path -Parent $PSScriptRoot
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

if ([string]::IsNullOrWhiteSpace($VaultPath)) {
    $VaultPath = Join-Path $env:USERPROFILE "Documents\Obsidian Vault"
}
if (-not (Test-Path -LiteralPath $VaultPath)) {
    throw "vault not found: $VaultPath"
}

$dst = Join-Path $VaultPath $FolderName

$ExcludeDirs = @(
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "htmlcov",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".claude",
    ".workbuddy",
    "frontend",
    "foxclaw_data",
    "test_data",
    "npm\python",
    "frontend_src\node_modules",
    "frontend_src\.vite",
    "foxclaw.egg-info"
)

$xd = @()
foreach ($d in $ExcludeDirs) {
    $xd += (Join-Path $src $d)
}

$rcArgs = @($src, $dst, "*.md", "/MIR", "/XD") + $xd + @(
    "/NJH", "/NJS", "/NDL", "/NP", "/R:1", "/W:1"
)
if (-not $Apply) {
    $rcArgs += "/L"
}

Write-Host ("source: {0}" -f $src)
Write-Host ("vault : {0}" -f $dst)
Write-Host ""

$out = & robocopy @rcArgs 2>&1
$rc = $LASTEXITCODE
if ($rc -ge 8) {
    $out | ForEach-Object { Write-Host $_ }
    throw "robocopy failed with exit code $rc"
}

$ownerPattern = [regex]::Escape("$dst\_")
$lines = @($out | ForEach-Object { $_.ToString().Trim() } |
    Where-Object { $_ -ne "" -and $_ -notmatch $ownerPattern })
$count = $lines.Count

if ($count -eq 0) {
    Write-Host "no changes."
} else {
    $lines | Select-Object -First 40 | ForEach-Object { Write-Host $_ }
    if ($count -gt 40) { Write-Host ("... {0} more" -f ($count - 40)) }
    Write-Host ("{0} entries" -f $count)
}

if (-not $Apply) {
    if ($count -gt 0) { Write-Host "`nrun with -Apply to write." }
    exit 0
}

$pruned = 0
$dirs = Get-ChildItem -LiteralPath $dst -Recurse -Directory -Force -ErrorAction SilentlyContinue |
    Sort-Object { $_.FullName.Length } -Descending
foreach ($d in $dirs) {
    if (@(Get-ChildItem -LiteralPath $d.FullName -Force -ErrorAction SilentlyContinue).Count -eq 0) {
        try {
            [IO.Directory]::Delete($d.FullName)
            $pruned++
        } catch {
        }
    }
}
if ($pruned -gt 0) { Write-Host ("pruned {0} empty dirs" -f $pruned) }

$mdCount = @(Get-ChildItem -LiteralPath $dst -Recurse -File -Filter *.md -ErrorAction SilentlyContinue).Count
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm"

$index = @"
# FoxClaw 镜像索引

由 ``tools/sync_obsidian.ps1`` 单向镜像生成,只取 ``.md``,不动代码与前端产物。

| 项 | 值 |
|---|---|
| 来源 | 本地 ``personal`` 分支工作区 |
| 备份远端 | 私有仓 ``foxclaw-dev``(remote ``dev``) |
| 生成时间 | $stamp |
| 源 md 文件数 | $mdCount |

## 说明

- **只读**:在本目录编辑不会回写到仓库,下次 ``-Apply`` 会覆盖;改文档请改源仓再同步。
- **由脚本托管**:``-Apply`` 会删除源仓已不存在的文件,手动笔记不要放这个目录。
- 含内部文档(``internal/``、``superpowers/``、``reports/``),仅存于本机与私有镜像,不外发。
- 顶层 ``README``/``CHANGELOG`` 与 ``docs/`` 下其余文件属公开内容,公开仓可见。
"@

Set-Content -LiteralPath (Join-Path $dst "_索引.md") -Value $index -Encoding utf8
Write-Host ("`nmirrored {0} md files -> {1}" -f $mdCount, $dst)
