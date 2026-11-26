"""屏幕 OCR 后端：Windows.Media.Ocr（经 powershell.exe，零 Python 依赖）。

先截屏（复用 capture）再喂给系统 OCR 引擎，返回整屏文本 + 逐词坐标框。
其它平台降级。
"""
import base64
import json
import os
import subprocess
import sys
import tempfile

from .capture import capture_screen


def _unsupported() -> dict:
    return {"ok": False, "available": False,
            "error": "OCR 暂仅支持 Windows（需 powershell.exe + Windows.Media.Ocr）"}


def ocr_available() -> bool:
    """Windows + powershell.exe 存在即视为可用（Windows.Media.Ocr 由系统自带）。"""
    return sys.platform == "win32"


def _run_powershell(script: str, env_extra: dict, timeout: int = 30) -> dict:
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
        return {"ok": False, "error": f"OCR 超时({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": (stderr or stdout)[:500]}
    if not stdout:
        return {"ok": False, "error": "OCR 脚本无输出"}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"OCR 输出非 JSON: {stdout[:200]}"}


_OCR_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapPixelFormat,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.FileAccessMode,Windows.Storage,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

$path = $env:FOXCLAW_OCR_IMAGE
$lang = $env:FOXCLAW_OCR_LANG

try {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $gray = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bitmap, [Windows.Graphics.Imaging.BitmapPixelFormat]::Gray8)

    $engine = $null
    if ($lang) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($lang))
    }
    if ($null -eq $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    }
    if ($null -eq $engine) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
    }
    if ($null -eq $engine) {
        [PSCustomObject]@{ ok = $false; available = $false; error = 'OCR 语言包未安装（Windows.Media.Ocr 无可用语言）' } | ConvertTo-Json
        return
    }

    $result = Await ($engine.RecognizeAsync($gray)) ([Windows.Media.Ocr.OcrResult])

    $lines = @()
    foreach ($ln in $result.Lines) {
        $words = @()
        foreach ($wd in $ln.Words) {
            $r = $wd.BoundingRect
            $words += [PSCustomObject]@{ text = [string]$wd.Text; rect = @([int]$r.X, [int]$r.Y, [int]$r.Width, [int]$r.Height) }
        }
        $lines += [PSCustomObject]@{ text = [string]$ln.Text; words = $words }
    }
    $fullText = ($result.Lines | ForEach-Object { [string]$_.Text }) -join "`n"

    [PSCustomObject]@{
        ok = $true
        text = $fullText
        language = [string]$engine.RecognizerLanguage.LanguageTag
        count = $lines.Count
        lines = $lines
    } | ConvertTo-Json -Depth 6
} catch {
    [PSCustomObject]@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json
}
"""


def ocr_sync(lang: str = "") -> dict:
    """截屏并 OCR。lang 如 'zh-CN'/'en-US'，空则按用户语言 + en-US 兜底。"""
    if sys.platform != "win32":
        return _unsupported()
    cap = capture_screen()
    if not cap.get("ok"):
        return {"ok": False, "error": cap.get("error", "截图失败")}
    png = base64.b64decode(cap["image"]["data"])
    fd, path = tempfile.mkstemp(suffix=".png")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png)
        return _run_powershell(
            _OCR_PS,
            {"FOXCLAW_OCR_IMAGE": path, "FOXCLAW_OCR_LANG": lang},
            timeout=30)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
