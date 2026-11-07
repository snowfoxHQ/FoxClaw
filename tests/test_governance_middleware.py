"""
M3 · 治理中间件回归测试

验证框架无关的 GovernanceSession / governed:
  1. 解耦:全程不构造 Foxclaw、不碰 agents/skills/channels/HMR,即可治理 + 审计;
  2. 工具越权确定性拦截(guard 抛 GovernanceBlocked);
  3. @governed 装饰器(同步 + 异步)在越权时阻止函数执行;
  4. 正常动作放行;审计链可取回且含 BLOCK 节点;
  5. 工具死循环检测。

用英文目标/内容做确定性断言(工具漂移本就确定性;语义维度对中文偏弱见 M1 评测)。
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from foxclaw.governance import GovernanceSession, governed, GovernanceBlocked


def check(cond, msg):
    if not cond:
        raise AssertionError(f"✗ {msg}")
    print(f"  ✓ {msg}")


def _sess():
    return GovernanceSession(
        goal="read the account balance and transactions only",
        allowed_tools={"read"}, entities={"balance", "account"})


def test_standalone_block():
    print("\n【1】独立可用 + 工具越权拦截")
    gov = _sess()
    # 正常只读:不抛(可能 PASS/WARN,但绝不 BLOCK)
    r_ok = gov.observe("call get_balance to read balance", tool="get_balance")
    check(not r_ok.blocked, "只读工具 get_balance 不被拦")
    # 越权:delete 不在 read 允许集 → 硬拦
    r_bad = gov.observe("call delete_account", tool="delete_account")
    check(r_bad.blocked and r_bad.dimension == "tool", "delete_account 被 BLOCK(维度=tool)")
    # guard 风格:越权抛异常
    raised = False
    try:
        gov.guard("call drop_table", tool="drop_table")
    except GovernanceBlocked as e:
        raised = e.result.blocked
    check(raised, "guard 对越权抛 GovernanceBlocked")


def test_decorator_sync_async():
    print("\n【2】@governed 装饰器(同步 + 异步)")
    gov = _sess()

    @governed(gov)
    def delete_row(x):
        return f"deleted {x}"      # 不该被执行

    executed = {"sync": False}
    try:
        delete_row("r1")
        executed["sync"] = True
    except GovernanceBlocked:
        pass
    check(not executed["sync"], "同步:越权工具函数被拦,未执行")

    @governed(gov, tool_name="delete_async")
    async def delete_async(x):
        return f"deleted {x}"

    async def run():
        try:
            await delete_async("r2")
            return True
        except GovernanceBlocked:
            return False
    check(not asyncio.run(run()), "异步:越权协程被拦,未执行")

    # 正常只读函数正常执行
    @governed(gov, tool_name="get_balance")
    def read_balance():
        return 100
    check(read_balance() == 100, "只读工具函数正常放行执行")


def test_audit_and_loop():
    print("\n【3】审计链 + 循环检测")
    gov = _sess()
    gov.observe("read balance", tool="get_balance")
    gov.observe("call delete_account", tool="delete_account")
    audit = gov.audit()
    check(len(audit) == 2, "审计链取回全部步骤")
    check(any(n["harness_status"] == "BLOCK" and n["tool_name"] == "delete_account"
              for n in audit), "越权步骤在审计链里标为 BLOCK")
    check(gov.summary()["blocked_this_session"] == 1, "摘要记录本会话拦截数")

    # 循环检测:反复同一工具 → loop 告警
    gov2 = _sess()
    loop_hit = None
    for _ in range(9):
        r = gov2.observe("poll status", tool="get_balance")
        if r.loop:
            loop_hit = r.loop
    check(loop_hit is not None, "反复同一工具触发循环检测告警")


def test_import_decoupling():
    print("\n【4】物理解耦:导入治理中间件不连带拉入 Agent 框架")
    # 必须在**全新子进程**里验(本进程里其它测试早已 import 了 agents,sys.modules 被污染)
    code = (
        "import sys, foxclaw.governance;"
        "bad=[m for m in ('foxclaw.agents.agents','foxclaw.skills',"
        "'foxclaw.channels','foxclaw.core.memory_adapter','foxclaw.orchestration')"
        " if m in sys.modules];"
        "assert not bad, ('leaked:', bad);"
        "assert 'foxclaw.harness.harness' in sys.modules;"
        "assert 'foxclaw.core.thought_chain' in sys.modules;"
        "print('DECOUPLED_OK')"
    )
    env = dict(os.environ, PYTHONUTF8="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=str(Path(__file__).parent.parent), env=env,
                       encoding="utf-8", errors="replace")
    check("DECOUPLED_OK" in r.stdout and r.returncode == 0,
          f"import foxclaw.governance 不拉入 agents/skills/channels/HMR（{r.stdout.strip()[:60]}{r.stderr.strip()[-80:]}）")


def main():
    print("=" * 56)
    print("M3 · 治理中间件回归")
    print("=" * 56)
    for t in (test_standalone_block, test_decorator_sync_async, test_audit_and_loop,
              test_import_decoupling):
        t()
    print("\n" + "=" * 56)
    print("治理中间件全部通过")
    print("=" * 56)


if __name__ == "__main__":
    main()
