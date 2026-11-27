"""UI Automation 后端：Windows .NET UIAutomationClient（经 powershell.exe，零 Python 依赖）。

感知：枚举前台窗口可交互元素（名称/类型/值/坐标/automation_id），扁平化 + index。
操作：invoke / set_value / select / toggle / expand / collapse / locate（供 click 取坐标）。
其它平台降级。
"""
import base64
import json
import os
import subprocess
import sys

# 交互元素类型白名单（排除 Window/Pane/ToolBar/ScrollBar 等纯结构/装饰控件）。
_INTERACTIVE_TYPES = [
    "Button", "Edit", "CheckBox", "RadioButton", "ComboBox", "List", "ListItem",
    "TreeItem", "MenuItem", "Text", "Hyperlink", "TabItem", "Spinner", "Slider",
    "DataItem", "HeaderItem", "SplitButton", "Document", "DataGrid", "Custom",
]

# 两个脚本里共用的「定位前台窗口 + 枚举交互元素」片段。
_FIND_ROOT_PS = r"""
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$root = [System.Windows.Automation.AutomationElement]::FocusedElement
$guard = 0
while ($root -ne $null -and $guard -lt 32) {
    if ($root.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window) { break }
    $root = $walker.GetParent($root)
    $guard++
}
"""

_INTERACTIVE_PS = "@('" + "','".join(_INTERACTIVE_TYPES) + "')"


def _unsupported() -> dict:
    return {"ok": False, "available": False,
            "error": "UI Automation 暂仅支持 Windows（需 powershell.exe + .NET UIAutomationClient）"}


def uia_available() -> bool:
    """Windows + powershell.exe 存在即视为可用（.NET UIA 程序集由 Windows 自带）。"""
    if sys.platform != "win32":
        return False
    return True


def _run_powershell(script: str, env_extra: dict, timeout: int = 20) -> dict:
    """经 -EncodedCommand(UTF-16LE base64) 执行脚本，参数走环境变量（避免拼接注入）。"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    env = os.environ.copy()
    env.update({k: str(v) for k, v in env_extra.items()})
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError:
        return {"ok": False, "available": False, "error": "powershell.exe 不可用"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"UI Automation 操作超时({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    # PowerShell 重定向 stdout 输出 UTF-8（系统 GBK 会把 0x80 continuation byte 判非法）。
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": (stderr or stdout)[:500]}
    if not stdout:
        return {"ok": False, "error": "UIA 脚本无输出"}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"UIA 输出非 JSON: {stdout[:200]}"}


_TREE_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
""" + _FIND_ROOT_PS + r"""
$interactive = """ + _INTERACTIVE_PS + r"""

if ($null -eq $root) {
    [PSCustomObject]@{ ok = $false; error = '无法定位前台窗口元素' } | ConvertTo-Json
    return
}

$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$elements = @()
$idx = 0
foreach ($el in $all) {
    if ($idx -ge 200) { break }
    $ct = $el.Current.ControlType.ProgrammaticName -replace '^ControlType\.', ''
    if ($interactive -notcontains $ct) { continue }
    if (-not $el.Current.IsEnabled) { continue }
    $r = $el.Current.BoundingRectangle
    $value = ''
    try {
        $vp = [System.Windows.Automation.ValuePattern]$el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $value = $vp.Current.Value
    } catch {}
    $elements += [PSCustomObject]@{
        index = $idx
        name = [string]$el.Current.Name
        type = $ct
        automation_id = [string]$el.Current.AutomationId
        class = [string]$el.Current.ClassName
        rect = @([int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height)
        value = [string]$value
    }
    $idx++
}

[PSCustomObject]@{ ok = $true; root = [string]$root.Current.Name; count = $elements.Count; elements = $elements } | ConvertTo-Json -Depth 6
"""

_ACTION_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$action = $env:FOXCLAW_UIA_ACTION
$value = $env:FOXCLAW_UIA_VALUE
$locatorJson = $env:FOXCLAW_UIA_LOCATOR
if (-not $locatorJson) { $locatorJson = '{}' }
$locator = $locatorJson | ConvertFrom-Json
""" + _FIND_ROOT_PS + r"""
$interactive = """ + _INTERACTIVE_PS + r"""

if ($null -eq $root) {
    [PSCustomObject]@{ ok = $false; error = '无法定位前台窗口元素' } | ConvertTo-Json
    return
}

$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$target = $null
$idx = 0
foreach ($el in $all) {
    if ($idx -ge 200) { break }
    $ct = $el.Current.ControlType.ProgrammaticName -replace '^ControlType\.', ''
    if ($interactive -notcontains $ct) { continue }
    if (-not $el.Current.IsEnabled) { continue }
    $matched = $false
    if ($null -ne $locator.index -and $idx -eq [int]$locator.index) { $matched = $true }
    elseif ($null -ne $locator.automation_id -and $el.Current.AutomationId -eq [string]$locator.automation_id) { $matched = $true }
    elseif ($null -ne $locator.name -and $el.Current.Name -eq [string]$locator.name) { $matched = $true }
    if ($matched) { $target = $el; break }
    $idx++
}

if ($null -eq $target) {
    [PSCustomObject]@{ ok = $false; error = '未找到匹配元素' } | ConvertTo-Json
    return
}

$result = $null
try {
    switch ($action) {
        'locate' {
            $r = $target.Current.BoundingRectangle
            $result = [PSCustomObject]@{
                ok = $true; action = 'locate'
                name = [string]$target.Current.Name
                type = [string]($target.Current.ControlType.ProgrammaticName -replace '^ControlType\.', '')
                rect = @([int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height)
                center = @([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2))
            }
        }
        'invoke' {
            $p = [System.Windows.Automation.InvokePattern]$target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
            $p.Invoke()
        }
        'set_value' {
            $p = [System.Windows.Automation.ValuePattern]$target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
            $p.SetValue($value)
        }
        'select' {
            $p = [System.Windows.Automation.SelectionItemPattern]$target.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
            $p.Select()
        }
        'toggle' {
            $p = [System.Windows.Automation.TogglePattern]$target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
            $p.Toggle()
        }
        'expand' {
            $p = [System.Windows.Automation.ExpandCollapsePattern]$target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
            $p.Expand()
        }
        'collapse' {
            $p = [System.Windows.Automation.ExpandCollapsePattern]$target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
            $p.Collapse()
        }
        default { $result = [PSCustomObject]@{ ok = $false; error = "未知 UIA 动作: $action" } }
    }
} catch {
    $result = [PSCustomObject]@{ ok = $false; error = $_.Exception.Message }
}

if ($null -eq $result) {
    $result = [PSCustomObject]@{ ok = $true; action = $action; name = [string]$target.Current.Name; type = [string]($target.Current.ControlType.ProgrammaticName -replace '^ControlType\.', '') }
}
$result | ConvertTo-Json
"""


def ui_tree_sync(target: str = "") -> dict:
    """枚举前台窗口的可交互元素树（扁平化 + index）。target 预留，首版聚焦前台。"""
    if sys.platform != "win32":
        return _unsupported()
    return _run_powershell(_TREE_PS, {}, timeout=20)


# 批量执行脚本(G4·UIA 批量):单进程顺序跑 N 个操作,每个独立定位、独立报错,
# 免去逐操作一次 powershell 往返的开销。授权仍在 desktop_act 边界——整批参数
# 作为一次动作完成授权与确认,批内不产生任何豁免。
_BATCH_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$opsJson = $env:FOXCLAW_UIA_OPS
if (-not $opsJson) { $opsJson = '[]' }
$ops = $opsJson | ConvertFrom-Json
""" + _FIND_ROOT_PS + r"""
$interactive = """ + _INTERACTIVE_PS + r"""

if ($null -eq $root) {
    [PSCustomObject]@{ ok = $false; error = '无法定位前台窗口元素' } | ConvertTo-Json
    return
}

function Find-Target($locator) {
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
    $idx = 0
    foreach ($el in $all) {
        if ($idx -ge 200) { break }
        $ct = $el.Current.ControlType.ProgrammaticName -replace '^ControlType\.', ''
        if ($interactive -notcontains $ct) { continue }
        if (-not $el.Current.IsEnabled) { continue }
        $matched = $false
        if ($null -ne $locator -and $null -ne $locator.index -and $idx -eq [int]$locator.index) { $matched = $true }
        elseif ($null -ne $locator -and $null -ne $locator.automation_id -and $el.Current.AutomationId -eq [string]$locator.automation_id) { $matched = $true }
        elseif ($null -ne $locator -and $null -ne $locator.name -and $el.Current.Name -eq [string]$locator.name) { $matched = $true }
        if ($matched) { return $el }
        $idx++
    }
    return $null
}

$results = @()
foreach ($op in $ops) {
    $a = [string]$op.action
    $target = Find-Target $op.locator
    if ($null -eq $target) {
        $results += [PSCustomObject]@{ ok = $false; action = $a; error = '未找到匹配元素' }
        continue
    }
    $err = ''
    try {
        switch ($a) {
            'locate' { }
            'invoke' {
                $p = [System.Windows.Automation.InvokePattern]$target.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
                $p.Invoke()
            }
            'set_value' {
                $p = [System.Windows.Automation.ValuePattern]$target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                $p.SetValue([string]$op.value)
            }
            'select' {
                $p = [System.Windows.Automation.SelectionItemPattern]$target.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
                $p.Select()
            }
            'toggle' {
                $p = [System.Windows.Automation.TogglePattern]$target.GetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern)
                $p.Toggle()
            }
            'expand' {
                $p = [System.Windows.Automation.ExpandCollapsePattern]$target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
                $p.Expand()
            }
            'collapse' {
                $p = [System.Windows.Automation.ExpandCollapsePattern]$target.GetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern)
                $p.Collapse()
            }
            default { $err = "未知 UIA 动作: $a" }
        }
    } catch {
        $err = $_.Exception.Message
    }
    if ($err) {
        $results += [PSCustomObject]@{ ok = $false; action = $a; error = $err }
    } else {
        $results += [PSCustomObject]@{ ok = $true; action = $a; name = [string]$target.Current.Name }
    }
}

[PSCustomObject]@{ ok = $true; count = $results.Count; results = $results } | ConvertTo-Json -Depth 5
"""

_BATCH_ALLOWED = ("invoke", "set_value", "select", "toggle", "expand", "collapse", "locate")
_BATCH_MAX = 20


def ui_batch_sync(ops) -> dict:
    """批量执行 UIA 操作(G4):单 powershell 进程顺序跑 N 个操作,每个独立定位/报错。

    ops: [{"action": "invoke"|"set_value"|...|"locate", "locator": {...}, "value": str}]
    有界(≤20 条);只收白名单动作;失败操作逐条回报,不中断整批(locate 类探测除外)。
    授权语义不变:整批在 desktop_act 边界作为一次动作完成授权与确认。
    """
    if sys.platform != "win32":
        return _unsupported()
    if not isinstance(ops, list):
        return {"ok": False, "error": "ui:batch 需要 ops 列表(如 [{'action':'invoke','locator':{'index':3}}])"}
    clean = []
    for op in ops[:_BATCH_MAX]:
        if not isinstance(op, dict):
            continue
        a = str(op.get("action") or "").strip()
        if a not in _BATCH_ALLOWED:
            continue
        loc = op.get("locator")
        clean.append({"action": a,
                      "locator": loc if isinstance(loc, dict) else {},
                      "value": str(op.get("value") or "")})
    if not clean:
        return {"ok": False,
                "error": (f"ui:batch 无有效操作(仅支持 {','.join(_BATCH_ALLOWED)},"
                          f"上限 {_BATCH_MAX} 条)")}
    return _run_powershell(
        _BATCH_PS,
        {"FOXCLAW_UIA_OPS": json.dumps(clean, ensure_ascii=False)},
        timeout=30)


def ui_action_sync(action: str, locator, value: str = "") -> dict:
    """对元素执行 UIA 操作。locator: {"index":N} / {"automation_id":X} / {"name":Y}。"""
    if sys.platform != "win32":
        return _unsupported()
    locator = locator if isinstance(locator, dict) else ({"index": int(locator)} if locator is not None else {})
    return _run_powershell(
        _ACTION_PS,
        {"FOXCLAW_UIA_ACTION": action,
         "FOXCLAW_UIA_LOCATOR": json.dumps(locator, ensure_ascii=False),
         "FOXCLAW_UIA_VALUE": value},
        timeout=15)
