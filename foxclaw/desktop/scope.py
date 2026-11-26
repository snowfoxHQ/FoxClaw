"""桌面 shell / 文件操作的范围限制(执行层兜底,所有入口共用)。

授权(check_action + 一次性确认码)回答「这个动作能不能做」;本模块回答「能在哪儿做」。
放在执行层而不是授权层:API、Agent 工具循环、将来的 Serve 回传执行,最后都落到
DesktopService.act,在这里卡一次就不会有漏网的入口。

规则:
  · 工作根目录 FOXCLAW_DESKTOP_ROOTS(os.pathsep 分隔,Windows 是 `;`):文件读/写/删与
    shell 的 cwd 必须落在其中。未设置时默认 = 用户主目录 + 系统临时目录。
  · 敏感路径永远拒绝(读也拒):密钥/凭据目录与文件、系统目录。可用
    FOXCLAW_DESKTOP_PROTECT 追加。
  · 路径先 resolve(展开 ~、..、符号链接)再判断,`a/../../.ssh` 绕不过去。
  · shell:危险命令黑名单 + 命令里出现敏感路径即拒绝 + 超时上限 + 输出截断。

边界(诚实):命令黑名单只能挡住常见的破坏性写法,挡不住刻意变形的命令——
shell:run 因此仍然默认 deny,这里是放开之后的第二道闸,不是让它变安全的理由。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

SHELL_TIMEOUT_MAX = 120
SHELL_OUTPUT_MAX = 64 * 1024

# 目录名(任一路径段命中即拒)
_SENSITIVE_DIRS = {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure", "gcloud",
                   ".password-store", "credentials"}
# 文件名(精确或通配)
_SENSITIVE_FILES = (".git-credentials", ".netrc", "_netrc", ".npmrc", ".pypirc",
                    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
                    "*.pem", "*.key", "*.pfx", "*.p12", "*.kdbx")
# Windows 系统目录
_SYSTEM_DIRS_WIN = ("windows", "program files", "program files (x86)", "programdata")
_SYSTEM_DIRS_POSIX = ("/etc", "/bin", "/sbin", "/usr", "/boot", "/sys", "/proc", "/dev")

_SHELL_DENY = [re.compile(p, re.I) for p in (
    r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r",      # rm -rf / rm -fr
    r"\b(del|erase)\s+.*/[sq]\b", r"\b(rd|rmdir)\s+.*/s\b",
    r"Remove-Item\b.*-Recurse",
    r"\bformat(\.com)?\s+[a-z]:", r"\bmkfs\b", r"\bdd\s+if=", r"\bdiskpart\b",
    r"\b(shutdown|reboot|halt|poweroff)\b", r"\b(Restart|Stop)-Computer\b",
    r"\breg(\.exe)?\s+(delete|add|import)\b", r"\b(vssadmin|bcdedit|wbadmin)\b",
    r"\bcipher(\.exe)?\s+/w", r"Set-ExecutionPolicy",
    r"\b(Invoke-Expression|iex)\b", r"-e(nc|ncodedcommand)?\s+[A-Za-z0-9+/=]{16,}",
    r"(curl|wget|iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n|]*\|\s*(sh|bash|zsh|iex|powershell|pwsh|cmd)\b",
    r":\(\)\s*\{\s*:\|:&\s*\};:",                            # fork bomb
)]


def _env_paths(name: str) -> list[Path]:
    raw = os.environ.get(name, "")
    return [Path(os.path.expanduser(p.strip())) for p in raw.split(os.pathsep) if p.strip()]


def roots() -> list[Path]:
    rs = _env_paths("FOXCLAW_DESKTOP_ROOTS") or [Path.home(), Path(tempfile.gettempdir())]
    out = []
    for r in rs:
        try:
            out.append(r.resolve())
        except OSError:
            continue
    return out


def _resolve(path: str) -> Path:
    return Path(os.path.expanduser(str(path))).resolve()


def _inside(p: Path, root: Path) -> bool:
    try:
        p.relative_to(root)
        return True
    except ValueError:
        return False


def _sensitive_reason(p: Path) -> str:
    import fnmatch
    parts = [s.lower() for s in p.parts]
    for d in _SENSITIVE_DIRS:
        if d in parts:
            return f"敏感目录 {d}"
    name = p.name.lower()
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "密钥配置文件 .env"
    for pat in _SENSITIVE_FILES:
        if fnmatch.fnmatch(name, pat):
            return f"凭据/密钥文件 {p.name}"
    if os.name == "nt":
        if len(parts) > 1 and parts[1].rstrip("\\/") in _SYSTEM_DIRS_WIN:
            return f"系统目录 {p.parts[1]}"
    else:
        sp = str(p)
        for d in _SYSTEM_DIRS_POSIX:
            if sp == d or sp.startswith(d + "/"):
                return f"系统目录 {d}"
    for extra in _env_paths("FOXCLAW_DESKTOP_PROTECT"):
        try:
            if _inside(p, extra.resolve()):
                return f"受保护路径 {extra}"
        except OSError:
            continue
    return ""


def check_path(path: str) -> tuple[bool, str, str]:
    """返回 (是否允许, 拒绝原因, 规范化后的绝对路径)。"""
    if not path or not str(path).strip():
        return False, "path 不能为空", ""
    try:
        p = _resolve(path)
    except (OSError, RuntimeError) as e:
        return False, f"路径无法解析: {type(e).__name__}", ""
    why = _sensitive_reason(p)
    if why:
        return False, f"{why},拒绝访问", str(p)
    rs = roots()
    if not any(_inside(p, r) for r in rs):
        return False, ("不在允许的工作目录内(FOXCLAW_DESKTOP_ROOTS: "
                       + os.pathsep.join(str(r) for r in rs) + ")"), str(p)
    return True, "", str(p)


def check_shell(command: str, cwd: str = "") -> tuple[bool, str, str]:
    """返回 (是否允许, 拒绝原因, 实际使用的 cwd)。cwd 为空时落到第一个工作根目录。"""
    cmd = str(command or "")
    if not cmd.strip():
        return False, "command 不能为空", ""
    for rx in _SHELL_DENY:
        if rx.search(cmd):
            return False, f"命中危险命令规则({rx.pattern[:40]})", ""
    low = cmd.lower()
    for d in _SENSITIVE_DIRS:
        if re.search(r"(^|[\\/\s\"'])" + re.escape(d) + r"([\\/\s\"']|$)", low):
            return False, f"命令涉及敏感目录 {d}", ""
    for name in (".git-credentials", ".netrc", ".pypirc", ".npmrc", "id_rsa", "id_ed25519"):
        if name in low:
            return False, f"命令涉及凭据文件 {name}", ""
    rs = roots()
    if not cwd:
        if not rs:
            return False, "没有可用的工作目录(FOXCLAW_DESKTOP_ROOTS)", ""
        return True, "", str(rs[0])
    ok, why, norm = check_path(cwd)
    if not ok:
        return False, f"cwd {why}", ""
    if not Path(norm).is_dir():
        return False, f"cwd 不是目录: {norm}", ""
    return True, "", norm


def clamp_timeout(v) -> int:
    try:
        t = int(v)
    except (TypeError, ValueError):
        t = 30
    return max(1, min(t, SHELL_TIMEOUT_MAX))


def truncate(text: str) -> tuple[str, bool]:
    s = text or ""
    if len(s) <= SHELL_OUTPUT_MAX:
        return s, False
    return s[:SHELL_OUTPUT_MAX], True
