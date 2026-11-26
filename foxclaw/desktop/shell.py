"""shell 执行后端：subprocess（跨平台，标准库）。"""
import subprocess


def shell_run_sync(command: str, cwd: str = "", timeout: int = 30) -> dict:
    if not command or not command.strip():
        return {"ok": False, "error": "command 不能为空"}
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd or None, timeout=timeout,
            capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"命令超时({timeout}s)"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}
