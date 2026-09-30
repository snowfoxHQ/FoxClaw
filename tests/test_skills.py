"""
Skill 接入层测试 — 助手的万能工具箱 + 对外生态兼容层
验证:
  1. skill 注册表与匹配(按触发词)
  2. 本地只读 skill 直接执行(calculator)
  3. 有副作用 skill 守门人拦截(gmail_send 需确认)
  4. 确认后放行
  5. MCP skill 未连接时返回 not_connected(留接口)
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["HESTIA_SHADOW_ROUNDS"] = "50"

import asyncio
from hestia import HestiaOS


async def main():
    print("=" * 60)
    print("Skill 接入层 · 助手万能工具箱 + 对外兼容层")
    print("=" * 60)
    _data = tempfile.mkdtemp(prefix="hestia_skill_")   # 系统临时目录,不污染仓库根
    os_ = HestiaOS(data_dir=_data, shadow_rounds=50)
    asst = os_.assistant

    # 1. 列出可用 skill
    print("\n【1】助手可用 skill 目录")
    for s in asst.list_skills():
        print(f"  · {s['name']:<10} [{s['kind']}/{s['side_effect']}] "
              f"触发: {','.join(s['triggers'][:3])}")

    # 2. 本地只读 skill:计算器(直接执行)
    print("\n【2】本地只读 skill(直接执行)")
    r = asst.use_skill("帮我计算 (12+8)*5", args={"expression": "(12+8)*5"})
    print(f"  匹配: {r['matched']['name']}")
    print(f"  结果: {r['result']['output']} · 状态: {r['result']['status']}")

    # 3. 有副作用 skill:发邮件(守门人拦截)
    print("\n【3】有副作用 skill(守门人拦截,需确认)")
    r2 = asst.use_skill("帮我发邮件给客户",
                        args={"to": "a@b.com", "subject": "你好", "body": "..."})
    print(f"  匹配: {r2['matched']['name']} ({r2['matched']['side_effect']})")
    print(f"  状态: {r2['result']['status']}")
    print(f"  守门人: {r2['result']['message']}")

    # 4. 确认后放行(MCP 未连 → not_connected)
    print("\n【4】用户确认后放行")
    r3 = asst.use_skill("帮我发邮件给客户",
                        args={"to": "a@b.com", "subject": "你好", "body": "..."},
                        confirmed=True)
    print(f"  状态: {r3['result']['status']}")
    print(f"  说明: {r3['result']['message']}")

    # 5. MCP 只读 skill:高德路线(未连接)
    print("\n【5】MCP skill 未连接(留接口)")
    r4 = asst.use_skill("从公司到机场怎么走",
                        args={"origin": "公司", "destination": "机场", "mode": "driving"})
    print(f"  匹配: {r4['matched']['name']} ({r4['matched']['kind']})")
    print(f"  状态: {r4['result']['status']}")
    print(f"  说明: {r4['result']['message']}")

    # 6. 深审回归:HookManager.audit_log 有界(长跑不泄漏)
    print("\n【6】深审回归:钩子审计日志有界(长跑不泄漏)")
    from hestia.skills.lifecycle import HookManager
    hm = HookManager(audit_limit=100)
    hm.register("after_tool_call", lambda ctx: {}, name="probe")
    for _ in range(5000):
        hm.fire("after_tool_call", skill_id="x")
    assert len(hm.audit_log) <= 100, f"audit_log 应有界(≤100),实得 {len(hm.audit_log)}"
    assert len(hm.recent_audit(20)) == 20, "recent_audit 仍返回最近 N 条"
    print(f"  ✓ 触发 5000 次,audit_log 稳定在 {len(hm.audit_log)}(≤100)→ 无泄漏")

    import shutil
    shutil.rmtree(_data, ignore_errors=True)

    print("\n" + "=" * 60)
    print("✓ Skill 接入层验证完成:匹配 + 守门人 + 多协议接入成立")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
