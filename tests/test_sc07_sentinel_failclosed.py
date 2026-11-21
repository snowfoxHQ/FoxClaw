"""SC-07·SentinelClient fail-closed 测试。

核验:守门人不可达时不存在绕过代理的执行路径——
  evaluate   → deny(reason_code=SENTINEL_UNAVAILABLE)
  mcp_call   → blocked(绝不落到主进程直连外部)
  execute_web → blocked(绝不退化为本地直发)
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.kernel.sentinel_client import SentinelClient
from foxclaw.skills import SkillSpec


def _dead_client(proc=None):
    """绕过 _spawn(不真正启动子进程),把客户端置于「守门人不可达」态。"""
    c = SentinelClient.__new__(SentinelClient)
    c._lock = threading.Lock()
    c._req_id = 0
    c._proc = proc
    return c


def _exited_proc():
    """真实启动后立即退出的子进程,模拟「守门人进程已退出」。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.exit(0)"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
    proc.wait()
    for stream in (proc.stdout, proc.stdin):
        try:
            stream.close()
        except Exception:
            pass
    return proc


def _close_pipes(proc):
    for stream in (proc.stdout, proc.stdin):
        try:
            stream.close()
        except Exception:
            pass


def _mcp_skill():
    return SkillSpec(id="m", name="m", description="m", kind="mcp",
                     mcp_server="srv", mcp_tool="t")


def _web_skill(secret_ref=""):
    return SkillSpec(id="w", name="w", description="w", kind="web_api",
                     endpoint="https://example.com", secret_ref=secret_ref)


def test_evaluate_unavailable_denies():
    d = _dead_client().evaluate(_mcp_skill(), {}, confirmed=True)
    assert d.decision == "deny"
    assert d.reason_code == "SENTINEL_UNAVAILABLE"
    assert d.stage == 0


def test_mcp_call_unavailable_blocked():
    r = _dead_client().mcp_call(_mcp_skill(), {})
    assert r["success"] is False and r["status"] == "blocked"
    assert "守门人不可达" in r["message"]


def test_execute_web_unavailable_blocked():
    r = _dead_client().execute_web(_web_skill(), {})
    assert r["success"] is False and r["status"] == "blocked"
    assert "守门人不可达" in r["message"]


def test_execute_web_secret_ref_unavailable_blocked():
    r = _dead_client().execute_web(_web_skill(secret_ref="api_key"), {})
    assert r["success"] is False and r["status"] == "blocked"
    assert "fail-closed" in r["message"]


def test_exited_process_fail_closed_all_ops():
    c = _dead_client(_exited_proc())
    assert c.evaluate(_mcp_skill(), {}, confirmed=True).reason_code == "SENTINEL_UNAVAILABLE"
    assert c.mcp_call(_mcp_skill(), {})["status"] == "blocked"
    assert c.execute_web(_web_skill(), {})["status"] == "blocked"


def test_spawned_guardian_dies_then_fail_closed():
    script = (
        "import sys\n"
        "for line in sys.stdin:\n"
        "    if '\"ping\"' in line:\n"
        "        sys.stdout.write('{\"id\":1,\"ok\":true,\"result\":{\"pong\":true}}\\n')\n"
        "        sys.stdout.flush()\n"
        "        sys.exit(0)\n"
    )
    c = SentinelClient(timeout=5.0, cmd=[sys.executable, "-c", script])
    try:
        d = c.evaluate(_mcp_skill(), {}, confirmed=True)
        assert d.decision == "deny" and d.reason_code == "SENTINEL_UNAVAILABLE"
        assert c.mcp_call(_mcp_skill(), {})["status"] == "blocked"
        assert c.execute_web(_web_skill(), {})["status"] == "blocked"
    finally:
        c.close()
        _close_pipes(c._proc)
