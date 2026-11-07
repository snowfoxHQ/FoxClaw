"""
Skill 接入层测试 — 助手的万能工具箱 + 对外生态兼容层
验证:
  1. skill 注册表与匹配(按触发词)
  2. 本地只读 skill 直接执行(calculator)
  3. 有副作用 skill 守门人拦截(gmail_send 需确认)
  4. 确认后放行
  5. MCP skill 未连接时返回 not_connected(留接口)
  6. 计算器 eval 加固(长度/连续乘方/指数/结果规模,防计算量攻击)
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["FOXCLAW_SHADOW_ROUNDS"] = "50"

import asyncio
from foxclaw import Foxclaw


async def main():
    print("=" * 60)
    print("Skill 接入层 · 助手万能工具箱 + 对外兼容层")
    print("=" * 60)
    _data = tempfile.mkdtemp(prefix="foxclaw_skill_")   # 系统临时目录,不污染仓库根
    os_ = Foxclaw(data_dir=_data, shadow_rounds=50)
    asst = os_.supervisor

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
    from foxclaw.skills.lifecycle import HookManager
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


def test_calc_hardening():
    """R2 加固回归:计算器 eval 的计算量攻击面。

    端到端的正常调用路径由 test_skills() 覆盖,这里直测 _demo_calc,
    针对 2026-10-25 新增的几道闸 —— 旧实现只有字符白名单,
    而白名单挡得住"取名字/取属性",挡不住 `9**9**9` 在 eval 阶段跑满 CPU 与内存。
    """
    from foxclaw.skills import _demo_calc

    # 正常路径不受影响
    assert _demo_calc("50*2")["result"] == 100
    assert _demo_calc("(12+8)*5")["result"] == 100
    assert _demo_calc("7/2")["result"] == 3.5
    assert _demo_calc("2**100")["result"] == 2 ** 100   # 指数 100 是允许的边界

    # 1. 字符白名单(旧有闸):字母/下划线被禁,取名字取属性进不来
    assert "error" in _demo_calc("__import__('os')")
    assert "error" in _demo_calc("a+b")
    assert "error" in _demo_calc("1 .bit_length")

    # 2. 长度闸:>200 字符直接拒绝,不进 eval
    long_expr = "1+" * 100 + "1"
    assert len(long_expr) == 201
    assert _demo_calc(long_expr)["error"] == "表达式过长"

    # 3. 连续乘方闸:右结合会得到天文级指数,必须先拦
    assert _demo_calc("9**9**9")["error"] == "不支持连续乘方"

    # 4. 指数闸:必须是不超过 100 的整数
    assert _demo_calc("2**101")["error"] == "乘方的指数需为不超过 100 的整数"
    assert _demo_calc("2**999999")["error"] == "乘方的指数需为不超过 100 的整数"
    assert _demo_calc("2**1.5")["error"] == "乘方的指数需为不超过 100 的整数"
    # 已知限制:带括号/运算的指数一律拒绝(宁可少算,不放行难校验的形态)
    assert _demo_calc("2**(3)")["error"] == "乘方的指数需为不超过 100 的整数"

    # 5. 求值期异常降级为 error,不向上抛
    assert "error" in _demo_calc("1/0")
    assert "error" in _demo_calc("")

    # 6. 结果规模兜底(>4096 bit 的 int、非有限 float)是防御性检查:
    #    上面三道闸之后已不可达(无字母造不出 inf,指数 ≤100 造不出 4096 bit),
    #    故此处只确认正常结果不被误伤,不给兜底本身造假用例。
    assert "error" not in _demo_calc("2**100")


def test_skills():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
